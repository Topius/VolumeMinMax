from flask import Flask, request, render_template
from datetime import datetime, timedelta
from collections import defaultdict
import schedule
import threading
import time
import logging
from flask_sqlalchemy import SQLAlchemy

# Configure basic logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)


app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coinsNEW.db'
db = SQLAlchemy(app)


# Initialize the coins_history with additional structure for monthly max/min volume
coins_history = defaultdict(lambda: {
    'current': [],
    'monthly_max_volume': {'volume': 0, 'price': None, 'timestamp': None},
    'monthly_min_volume': {'volume': float('inf'), 'price': None, 'timestamp': None},
    '24_hour_max_volume': {'volume': 0, 'price': None, 'timestamp': None},
    '24_hour_min_volume': {'volume': float('inf'), 'price': None, 'timestamp': None},
    '-30mins': None, '-1hour': None, '-1.5hours': None, '-2hours': None, '-12hours': None, 'yesterday': None
})


# Unformat volume for comparisons
def unformat_volume(volume_str):
    volume_str = volume_str.replace(',', '')
    factors = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3, '': 1}
    for suffix, factor in factors.items():
        if volume_str.endswith(suffix):
            return float(volume_str.replace(suffix, '')) * factor
    return float(volume_str)

class CoinHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coin_name = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    volume = db.Column(db.String(20))
    change = db.Column(db.String(20))
    direction = db.Column(db.String(20))
    price = db.Column(db.String(20))

    def __repr__(self):
        return f'<CoinHistory {self.coin_name}>'


def update_history_with_new_data(coin, data):
    if coin is None or data is None:
        logging.info("Skipping update due to None coin or data.")
        return

    logging.debug(f"Updating history for coin: {coin}")

    current_time = datetime.utcnow()
    history = coins_history[coin]

    # Insert new data at the beginning of the 'current' list
    if 'current' not in history:
        history['current'] = []
    history['current'].insert(0, {
        'timestamp': data['timestamp'],
        'change': data['change'],
        'volume_short': format_volume(data['volume']),
        'direction': data['direction'],
        'price': data['price'],  # Including the price in the history
        'volume': data['volume']  # Adding the volume attribute
    })

    # Keep only the 10 most recent entries for 'current'
    if len(history['current']) > 10:
        history['current'] = history['current'][:10]

    # Update the historical intervals with the closest data point to their respective marks
    time_intervals = [30, 60, 90, 120, 720, 1440]  # in minutes
    keys = ['-30mins', '-1hour', '-1.5hours', '-2hours', '-12hours', 'yesterday']

    for i, (key, minutes) in enumerate(zip(keys, time_intervals)):
        mark = current_time - timedelta(minutes=minutes)
        closest_to_mark = min(
            (entry for entry in history['current'] if entry['timestamp'] <= mark),
            key=lambda x: abs((x['timestamp'] - mark).total_seconds()),
            default=None
        )
        if closest_to_mark:
            history[key] = closest_to_mark

    # Update 24-hour min and max volumes based on the data within the last 24 hours
    last_24_hours_data = [entry for entry in history['current'] if current_time - entry['timestamp'] <= timedelta(hours=24)]
    if last_24_hours_data:
        volumes = [entry['volume'] for entry in last_24_hours_data]
        logging.debug(f"Volumes within the last 24 hours for {coin}: {volumes}")
        min_volume = min(volumes)
        max_volume = max(volumes)

        # Update min and max volumes if data exists
        if min_volume != float('inf'):
            history['24_hour_min_volume'] = {'volume': min_volume, 'price': data['price'], 'timestamp': data['timestamp']}
        if max_volume != 0:
            history['24_hour_max_volume'] = {'volume': max_volume, 'price': data['price'], 'timestamp': data['timestamp']}

        # Update monthly min and max volumes
        if min_volume < history['monthly_min_volume']['volume']:
            history['monthly_min_volume'] = {'volume': min_volume, 'price': data['price'], 'timestamp': data['timestamp']}
        if max_volume > history['monthly_max_volume']['volume']:
            history['monthly_max_volume'] = {'volume': max_volume, 'price': data['price'], 'timestamp': data['timestamp']}

    logging.debug(f"Updated history for coin: {coin}: {history}")



def format_volume(volume, short=False):
    if volume >= 1e12:
        return f'{volume / 1e12:.2f}T' if short else f'{volume / 1e12:.2f} T'
    elif volume >= 1e9:
        return f'{volume / 1e9:.2f}B' if short else f'{volume / 1e9:.2f} B'
    elif volume >= 1e6:
        return f'{volume / 1e6:.2f}M' if short else f'{volume / 1e6:.2f} M'
    elif volume >= 1e3:
        return f'{volume / 1e3:.2f}K' if short else f'{volume / 1e3:.2f} K'
    return str(volume)


# Remaining functions (get_time_key, Flask route handlers) remain unchanged.


def get_time_key(time_diff):
    if time_diff < timedelta(minutes=30):
        return '-30mins'
    elif time_diff < timedelta(hours=1):
        return '-1hour'
    elif time_diff < timedelta(hours=1.5):
        return '-1.5hours'
    elif time_diff < timedelta(hours=2):
        return '-2hours'
    elif time_diff < timedelta(hours=12):
        return '-12hours'
    elif time_diff < timedelta(days=1):
        return 'yesterday'
    return None


@app.route('/')
def index():
    current_time = datetime.utcnow()
    prepared_data = {}

    # Initialize the time slots for each coin, including monthly max and min
    time_keys = ['current', '-30mins', '-1hour', '-1.5hours', '-2hours', '-12hours', 'yesterday', 'monthly_max_volume', 'monthly_min_volume']
    for coin, history in coins_history.items():
        prepared_history = {k: history.get(k, None) for k in time_keys}

        # Process 'current' data and find closest to 30 minutes mark if not already processed
        current_data_list = prepared_history.get('current', [])
        if current_data_list:
            thirty_min_mark = current_time - timedelta(minutes=30)
            closest_to_thirty_min = min(current_data_list, key=lambda x: abs((x['timestamp'] - thirty_min_mark).total_seconds()), default=None)
            prepared_history['-30mins'] = closest_to_thirty_min or prepared_history.get('-30mins')

        # Format min and max 24-hour volumes for display
        prepared_history['Min 24h/V'] = format_volume(history.get('24_hour_min_volume', {'volume': float('inf')})['volume'])
        prepared_history['Max 24h/V'] = format_volume(history.get('24_hour_max_volume', {'volume': 0})['volume'])

        # Include volume and price for monthly max and min volumes
        prepared_history['Monthly Max Volume'] = f"{format_volume(history.get('monthly_max_volume', {}).get('volume', 0), short=True)} at {history.get('monthly_max_volume', {}).get('price', 'Unavailable')}"
        prepared_history['Monthly Min Volume'] = f"{format_volume(history.get('monthly_min_volume', {}).get('volume', float('inf')), short=True)} at {history.get('monthly_min_volume', {}).get('price', 'Unavailable')}"

        prepared_data[coin] = prepared_history

    return render_template('index.html', coins_history=prepared_data, current_time=current_time, format_volume=format_volume)



@app.route('/update_coin', methods=['POST'])
def update_coin():
    coin_info = request.json
    coin_name = coin_info['name'].lower()

    # Extract volume from the JSON data and remove commas before converting to float
    volume_str = coin_info.get('volume', "0").replace(',', '')
    try:
        volume = float(volume_str)
    except ValueError as e:
        logging.error(f"Error converting volume to float: {e}")
        return "Error processing volume", 400

    logging.debug(f"Received coin info: {coin_info}, volume: {volume}")

    coin_info['timestamp'] = datetime.utcnow()
    coin_info['volume'] = volume  # Volume is now a float
    coin_info['volume_short'] = format_volume(coin_info['volume'])
    coin_info['price'] = coin_info.get('price', 'Unavailable')

    # Update the history with the new data
    update_history_with_new_data(coin_name, coin_info)
    return '', 204



def schedule_update():
    # Schedule the update_history_with_new_data function to run every minute
    schedule.every().minute.do(lambda: update_history_with_new_data(coin=None, data=None))

    # Keep running the scheduler
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Create the tables if they don't exist
    threading.Thread(target=schedule_update).start()
    app.run(debug=True, port=5000)

