from flask import Flask, request, render_template
from datetime import datetime, timedelta
from collections import defaultdict
import schedule
import threading
import time
import logging
from flask_sqlalchemy import SQLAlchemy
import json

# Configure basic logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


COINS_HISTORY_FILE = 'coins_history.txt'  # Define the file path

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coinsNEW.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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


def save_coins_history():
    with open(COINS_HISTORY_FILE, 'w') as f:
        # Convert the defaultdict to a regular dictionary for JSON serialization
        data_to_save = {coin: history for coin, history in coins_history.items()}
        json.dump(data_to_save, f, default=str)  # Use default=str to handle datetime serialization


def load_coins_history():
    try:
        with open(COINS_HISTORY_FILE, 'r') as f:
            # Load the data from the file
            data_loaded = json.load(f)

            # Convert strings back to datetime objects
            for coin, history in data_loaded.items():
                for entry in history['current']:
                    entry['timestamp'] = datetime.fromisoformat(entry['timestamp'])

            # Replace the existing coins_history with the loaded data
            coins_history.update(data_loaded)

        # print("Loaded coin history:", coins_history)  # Add this print statement SPAM DEBUG UPDATED LIST

    except FileNotFoundError:
        logging.info(f"{COINS_HISTORY_FILE} not found. Starting with an empty coins_history.")



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
    history['current'].insert(0, {
        'timestamp': data['timestamp'],
        'change': data['change'],
        'volume_short': format_volume(data['volume']),
        'direction': data['direction'],
        'price': data['price'],
        'volume': data['volume']
    })

    # Keep only the 100 most recent entries for 'current'
    history['current'] = history['current'][:300]

    # Calculate days ago for monthly max/min
    def calculate_days_ago(timestamp):
        return (current_time.date() - timestamp.date()).days

    # Update historical intervals and monthly min/max volumes
    for minutes, key in zip([30, 60, 90, 120, 720, 1440], ['-30mins', '-1hour', '-1.5hours', '-2hours', '-12hours', 'yesterday']):
        mark = current_time - timedelta(minutes=minutes)
        past_entries = [entry for entry in history['current'] if entry['timestamp'] <= mark]
        closest_entry = min(past_entries, key=lambda entry: abs((entry['timestamp'] - mark).total_seconds()), default=None)
        history[key] = closest_entry

    # Update 24-hour and monthly min/max volumes
    recent_entries = [entry for entry in history['current'] if current_time - entry['timestamp'] <= timedelta(days=1)]
    if recent_entries:
        volumes = [entry['volume'] for entry in recent_entries]
        min_volume_entry = min(recent_entries, key=lambda entry: entry['volume'])
        max_volume_entry = max(recent_entries, key=lambda entry: entry['volume'])

        history['24_hour_min_volume'] = min_volume_entry
        history['24_hour_max_volume'] = max_volume_entry

        # Check and update monthly volumes if necessary
        if min_volume_entry['volume'] < history['monthly_min_volume'].get('volume', float('inf')):
            history['monthly_min_volume'] = min_volume_entry
            history['monthly_min_volume']['days_ago'] = calculate_days_ago(min_volume_entry['timestamp'])

        if max_volume_entry['volume'] > history['monthly_max_volume'].get('volume', 0):
            history['monthly_max_volume'] = max_volume_entry
            history['monthly_max_volume']['days_ago'] = calculate_days_ago(max_volume_entry['timestamp'])

    save_coins_history()
    logging.debug(f"Updated history for coin: {coin}")




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


def sync_file_data_with_db():
    try:
        with open(COINS_HISTORY_FILE, 'r') as f:
            data_loaded = json.load(f)
            for coin, history_list in data_loaded.items():
                for entry in history_list.get('current', []):  # Assuming 'current' holds the relevant entries
                    # Parse the timestamp string to a datetime object
                    timestamp = datetime.fromisoformat(entry['timestamp'])
                    # Check if this entry already exists in the DB
                    exists = CoinHistory.query.filter_by(coin_name=coin, timestamp=timestamp).first()
                    if not exists:
                        # Entry does not exist, so we insert it
                        new_entry = CoinHistory(
                            coin_name=coin,
                            timestamp=timestamp,
                            volume=entry['volume'],
                            change=entry['change'],
                            direction=entry['direction'],
                            price=entry['price']
                        )
                        db.session.add(new_entry)
            db.session.commit()
    except FileNotFoundError:
        logging.info(f"{COINS_HISTORY_FILE} not found. No data to sync with DB.")
    except Exception as e:
        logging.error(f"Error syncing file data with DB: {str(e)}")



@app.route('/')
def index():
    current_time = datetime.utcnow()
    prepared_data = {}

    # Initialize the time slots for each coin, including monthly max and min
    time_keys = ['current', '-30mins', '-1hour', '-1.5hours', '-2hours', '-12hours', 'yesterday', 'monthly_max_volume',
                 'monthly_min_volume']
    for coin, history in coins_history.items():
        prepared_history = {k: history.get(k, None) for k in time_keys}

        # Process 'current' data and find closest to 30 minutes mark if not already processed
        current_data_list = prepared_history.get('current', [])
        if current_data_list:
            thirty_min_mark = current_time - timedelta(minutes=30)
            closest_to_thirty_min = min(current_data_list,
                                        key=lambda x: abs((x['timestamp'] - thirty_min_mark).total_seconds()),
                                        default=None)
            prepared_history['-30mins'] = closest_to_thirty_min or prepared_history.get('-30mins')

        # Format min and max 24-hour volumes for display
        prepared_history['Min 24h/V'] = format_volume(
            history.get('24_hour_min_volume', {'volume': float('inf')})['volume'])
        prepared_history['Max 24h/V'] = format_volume(history.get('24_hour_max_volume', {'volume': 0})['volume'])

        # Update days ago for monthly volumes
        if history.get('monthly_max_volume'):
            timestamp_max = history['monthly_max_volume']['timestamp']
            # Check if the timestamp is a string, and parse it if so
            if isinstance(timestamp_max, str):
                timestamp_max = datetime.fromisoformat(timestamp_max)
            prepared_history['monthly_max_volume']['days_ago'] = (current_time.date() - timestamp_max.date()).days

        if history.get('monthly_min_volume'):
            timestamp_min = history['monthly_min_volume']['timestamp']
            # Check if the timestamp is a string, and parse it if so
            if isinstance(timestamp_min, str):
                timestamp_min = datetime.fromisoformat(timestamp_min)
            prepared_history['monthly_min_volume']['days_ago'] = (current_time.date() - timestamp_min.date()).days

        prepared_data[coin] = prepared_history

    return render_template('index.html', coins_history=prepared_data, current_time=current_time,
                           format_volume=format_volume)


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

    # Prepare the data for the history and the database
    coin_data_for_history = {
        'timestamp': datetime.utcnow(),
        'change': coin_info.get('change', ''),
        'volume_short': format_volume(volume),
        'direction': coin_info.get('direction', ''),
        'price': coin_info.get('price', 'Unavailable'),
        'volume': volume
    }

    # Update the in-memory history
    update_history_with_new_data(coin_name, coin_data_for_history)

    # Persist the new coin data to the database
    new_coin_history_entry = CoinHistory(
        coin_name=coin_name,
        timestamp=coin_data_for_history['timestamp'],
        volume=volume_str,  # storing the original string format
        change=coin_data_for_history['change'],
        direction=coin_data_for_history['direction'],
        price=coin_data_for_history['price']
    )
    db.session.add(new_coin_history_entry)
    db.session.commit()

    logging.info(f"Data for {coin_name} updated successfully.")
    return '', 204




def schedule_update():
    # Schedule the update_history_with_new_data function to run every minute
    schedule.every().minute.do(lambda: update_history_with_new_data(coin=None, data=None))

    # Keep running the scheduler
    while True:
        schedule.run_pending()
        time.sleep(1)


def debug_print_coin_history(coin_name):
    coin_history = coins_history.get(coin_name.lower())
    if not coin_history:
        print(f"No history found for coin: {coin_name}")
        return

    print(f"Debugging history for coin: {coin_name}")
    for key, value in coin_history.items():
        if isinstance(value, list):
            print(
                f"{key}: {[{'timestamp': entry['timestamp'].isoformat(), 'volume': entry['volume'], 'price': entry['price']} for entry in value]}")
        else:
            print(f"{key}: {value}")


# Call this function after each update to print out the history
debug_print_coin_history('BITCOIN')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Create the tables if they don't exist already
        load_coins_history()  # Load the coins history from the file
        sync_file_data_with_db()  # Sync data from the file with the DB
    threading.Thread(target=schedule_update).start()
    app.run(host='0.0.0.0', port=5000, debug=True)


