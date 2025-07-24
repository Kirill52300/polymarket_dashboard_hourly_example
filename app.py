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
            # Call parent method to update the dictionary
            super().update_global_price_dict()
            
            # Convert to list for dashboard (each market as separate item)
            dashboard_data = []
            for market_key, market_data in self.global_price_dict.items():
                dashboard_data.append(market_data)
            
            print(f"Emitting {len(dashboard_data)} markets to dashboard")
            
            # Emit data to dashboard
            self.socketio.emit('price_update', {
                'data': dashboard_data
            })
        except Exception as e:
            print(f"Error updating price dict: {e}")
            import traceback
            traceback.print_exc()

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
        dashboard_data = []
        for market_key, market_data in tracker.global_price_dict.items():
            dashboard_data.append(market_data)
        
        emit('price_update', {
            'data': dashboard_data
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
        print("Dashboard will be available at: http://localhost:5005")
        print("Test endpoint available at: http://localhost:5005/test")
        
        # Start with simpler configuration first
        socketio.run(app, debug=False, host='0.0.0.0', port=5005, allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"Error starting Flask app: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
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
