from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import asyncio
import threading
import json
import sys
import os

# Add current directory to path to import poly_m
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

print("Python version:", sys.version)
print("Current working directory:", os.getcwd())
print("Files in directory:", os.listdir('.'))

try:
    print("Attempting to import poly_m...")
    from poly_m import CryptoPriceTracker
    print("Successfully imported CryptoPriceTracker")
except ImportError as e:
    print(f"Error importing poly_m: {e}")
    print("Make sure poly_m.py exists in the same directory")
    print("Files in current directory:", [f for f in os.listdir('.') if f.endswith('.py')])
    sys.exit(1)

# Test Flask imports
try:
    print("Testing Flask imports...")
    app = Flask(__name__)
    print("Flask app created successfully")
    
    app.config['SECRET_KEY'] = 'your-secret-key'
    socketio = SocketIO(app, cors_allowed_origins="*")
    print("SocketIO initialized successfully")
except Exception as e:
    print(f"Error initializing Flask/SocketIO: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Global tracker instance
tracker = None
tracker_thread = None

class DashboardTracker(CryptoPriceTracker):
    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio
    
    def update_global_price_dict(self):
        """Override to emit data to dashboard instead of printing"""
        try:
            for symbol in self.symbols:
                current_price = self.prices.get(symbol, {}).get("price")
                trigger_data = self.trigger_prices.get(symbol, {})
                trigger_price = trigger_data.get("price") if isinstance(trigger_data, dict) else trigger_data
                market_title = trigger_data.get("market_title", "Unknown Market") if isinstance(trigger_data, dict) else "Unknown Market"
                
                # Calculate differences
                price_diff = None
                price_diff_percent = None
                if current_price and trigger_price:
                    price_diff = current_price - trigger_price
                    price_diff_percent = (price_diff / trigger_price) * 100
                
                # Get existing data to preserve UP/DOWN prices
                existing_data = self.global_price_dict.get(symbol, {})
                
                self.global_price_dict[symbol] = {
                    "symbol": symbol,
                    "market_title": market_title,
                    "current_price": current_price,
                    "trigger_price": trigger_price,
                    "price_difference": price_diff,
                    "price_difference_percent": price_diff_percent,
                    "last_updated": self.prices.get(symbol, {}).get("timestamp"),
                    "up_price": existing_data.get("up_price"),
                    "down_price": existing_data.get("down_price"),
                    "orderbook_updated": existing_data.get("orderbook_updated")
                }
            
            # Emit data to dashboard
            self.socketio.emit('price_update', {
                'data': list(self.global_price_dict.values())
            })
        except Exception as e:
            print(f"Error updating price dict: {e}")

def run_tracker_async():
    """Run the tracker in async event loop"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        global tracker
        tracker = DashboardTracker(socketio)
        
        print("Starting crypto tracker...")
        loop.run_until_complete(tracker.start_tracking())
    except Exception as e:
        print(f"Tracker error: {e}")
        import traceback
        traceback.print_exc()

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/test')
def test():
    return "Flask is working!"

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    # Send current data if available
    if tracker and tracker.global_price_dict:
        emit('price_update', {
            'data': list(tracker.global_price_dict.values())
        })

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('start_tracking')
def handle_start_tracking():
    global tracker_thread
    try:
        if tracker_thread is None or not tracker_thread.is_alive():
            print("Starting new tracker thread...")
            tracker_thread = threading.Thread(target=run_tracker_async)
            tracker_thread.daemon = True
            tracker_thread.start()
            emit('status', {'message': 'Tracking started'})
        else:
            emit('status', {'message': 'Tracking already running'})
    except Exception as e:
        print(f"Error starting tracker: {e}")
        emit('status', {'message': f'Error starting tracker: {e}'})

if __name__ == '__main__':
    try:
        print("All imports successful!")
        print("Starting Flask-SocketIO server...")
        print("Dashboard will be available at: http://localhost:5000")
        print("Test endpoint available at: http://localhost:5000/test")
        
        # Start with simpler configuration first
        socketio.run(app, debug=False, host='0.0.0.0', port=5005, allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"Error starting Flask app: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
