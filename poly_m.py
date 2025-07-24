import asyncio
import websockets
import json
import aiohttp
import re
from typing import Dict, Set
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

@dataclass
class BookParams:
    token_id: str
    side: str = ""

class CryptoPriceTracker:
    def __init__(self):
        self.prices: Dict[str, Dict] = {}
        self.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]  # Default symbols
        self.active_symbols: Set[str] = set()  # Symbols from markets ending within 1 hour
        self.connections = {}
        self.update_counts = {}
        self.hourly_markets = []
        self.all_markets = {}  # Store all markets with ID as key
        self.trigger_prices = {}  # Store trigger prices for symbols
        self.global_price_dict = {}  # Global dictionary with current and trigger prices
        self.market_tokens = {}  # Store market tokens for order book requests
        self.order_book_task = None  # Task for order book fetching
        
    async def fetch_polymarket_events(self):
        """Fetch events from Polymarket API and filter for up-or-down-hourly markets"""
        url = "https://gamma-api.polymarket.com/events/pagination?limit=10000&active=true&archived=false&tag_slug=crypto&closed=false&order=volume24hr&ascending=false&offset=0"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        await self.process_events(data, session)
                    else:
                        print(f"Failed to fetch events: HTTP {response.status}")
        except Exception as e:
            print(f"Error fetching events: {e}")
    
    async def process_events(self, data, session):
        """Process events and filter for up-or-down-hourly series, collect all markets"""
        events = data.get("data", [])
        temp_symbols = set()
        
        for event in events:
            # Check for hourly series first
            has_hourly_series = False
            series = event.get("series", [])
            for serie in series:
                if "up-or-down-hourly" in serie.get("slug", ""):
                    has_hourly_series = True
                    break
            
            # Collect markets only if event has hourly series
            if has_hourly_series:
                markets = event.get("markets", [])
                for market in markets:
                    market_id = market.get("id")
                    if market_id:
                        # Check if market ends within 1 hour
                        market_end_date = market.get("endDate")
                        
                        if market_end_date:
                            try:
                                end_date = datetime.fromisoformat(market_end_date.replace('Z', '+00:00'))
                                now = datetime.now(timezone.utc)
                                time_diff = end_date - now
                                seconds_remaining = time_diff.total_seconds()
                                
                                if 0 < seconds_remaining <= 3600:  # 5 hours = 18000 seconds
                                    self.all_markets[market_id] = market
                                    
                                    # Extract symbol from resolution source
                                    symbol = self.extract_symbol_from_resolution_source(market.get("resolutionSource", ""))
                                    if symbol:
                                        temp_symbols.add(symbol)
                                        
                                        # Store first token ID for order book requests
                                        clob_token_ids = market.get("clobTokenIds", [])
                                        if clob_token_ids and len(clob_token_ids) > 0:
                                            # Parse the JSON string to get the first token
                                            try:
                                                token_list = json.loads(clob_token_ids) if isinstance(clob_token_ids, str) else clob_token_ids
                                                if token_list and len(token_list) > 0:
                                                    self.market_tokens[symbol] = {
                                                        "token_id": token_list[0],
                                                        "market_title": market.get("question", "Unknown Market"),
                                                        "market_id": market_id
                                                    }
                                            except (json.JSONDecodeError, IndexError):
                                                print(f"Error parsing token IDs for market {market_id}")
                                        
                                        # Get trigger price
                                        start_date = market.get("eventStartTime")
                                        if start_date:
                                            trigger_price = await self.get_trigger_price(session, symbol, start_date)
                                            if trigger_price:
                                                self.trigger_prices[symbol] = {
                                                    "price": trigger_price,
                                                    "market_title": market.get("question", "Unknown Market")
                                                }
                            except Exception as e:
                                continue
        
        # Update active symbols
        self.active_symbols = temp_symbols
        if self.active_symbols:
            self.symbols = list(self.active_symbols)
        
        # Reset update counts for new symbols
        self.update_counts = {symbol: 0 for symbol in self.symbols}
        
        # Initialize global price dictionary
        self.update_global_price_dict()
        
    async def get_trigger_price(self, session, symbol, start_date):
        """Get trigger price from Polymarket API"""
        try:
            # Extract base symbol (BTC from BTCUSDT)
            base_symbol = symbol.replace("USDT", "")
            url = f"https://polymarket.com/api/crypto/crypto-price?symbol={base_symbol}&eventStartTime={start_date}"
            
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("openPrice")
        except Exception as e:
            print(f"Error getting trigger price for {symbol}: {e}")
        return None
    
    def extract_symbol_from_resolution_source(self, resolution_source):
        """Extract crypto symbol from resolution source URL"""
        if not resolution_source:
            return None
            
        try:
            # Look for patterns like BTC_USDT, ETH_USDT etc in the URL
            match = re.search(r'/([A-Z]+)_USDT', resolution_source)
            if match:
                crypto_symbol = match.group(1)
                return f"{crypto_symbol}USDT"
            
            # Alternative pattern without underscore
            match = re.search(r'/([A-Z]+)USDT', resolution_source)
            if match:
                return match.group(0)[1:]
                
            return None
        except Exception as e:
            return None

    async def fetch_order_books(self):
        """Fetch order books for all active markets every 2 seconds"""
        while True:
            try:
                if not self.market_tokens:
                    await asyncio.sleep(2)
                    continue
                
                # Prepare parameters for all tokens with mapping
                params = []
                token_to_symbol_map = {}
                
                for symbol, token_data in self.market_tokens.items():
                    token_id = token_data["token_id"]
                    params.append(BookParams(token_id=token_id))
                    token_to_symbol_map[token_id] = symbol
                
                if not params:
                    await asyncio.sleep(2)
                    continue
                
                # Convert to dictionary format for JSON
                params_dict = [asdict(param) for param in params]
                
                url = "https://clob.polymarket.com/books"
                headers = {
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=params_dict, headers=headers, timeout=10) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            await self.process_order_books(response_data, token_to_symbol_map)
                        else:
                            print(f"Order book request failed: {response.status}")
                
            except Exception as e:
                print(f"Error fetching order books: {e}")
            
            await asyncio.sleep(2)  # Wait 2 seconds before next request

    async def process_order_books(self, order_books, token_to_symbol_map):
        """Process order book data and update UP/DOWN prices"""
        try:
            # order_books is a list where each item corresponds to the token we requested
            # We need to match the order of response with the order of our request
            
            for i, book in enumerate(order_books):
                # Get the token_id from our original request parameters
                if i < len(self.market_tokens):
                    # Get token_id by index from our market_tokens
                    token_ids = list(self.market_tokens.values())
                    if i < len(token_ids):
                        token_id = token_ids[i]["token_id"]
                        symbol = token_to_symbol_map.get(token_id)
                        
                        if not symbol:
                            continue
                        
                        # Get bids and asks
                        bids = book.get('bids', [])
                        asks = book.get('asks', [])
                        
                        up_price = None
                        down_price = None
                     #   print(bids)
                        
                        # Sort and get best prices
                        if asks:
                            asks_sorted = sorted(asks, key=lambda x: float(x.get('price', 0)))
                            lowest_ask_price = float(asks_sorted[0].get('price', 0))
                            up_price = lowest_ask_price  # UP = lowest ask
                        
                        if bids:
                            bids_sorted = sorted(bids, key=lambda x: float(x.get('price', 0)), reverse=True)
                            highest_bid_price = float(bids_sorted[0].get('price', 0))
                            down_price = 1 - highest_bid_price  # DOWN = 1 - highest bid
                        
                        print(f"Order book for {symbol}: UP={up_price}, DOWN={down_price}")
                        
                        # Update global price dictionary with UP/DOWN prices
                        if symbol in self.global_price_dict:
                            self.global_price_dict[symbol].update({
                                "up_price": up_price,
                                "down_price": down_price,
                                "orderbook_updated": datetime.now().isoformat()
                            })
                
        except Exception as e:
            print(f"Error processing order books: {e}")

    def update_global_price_dict(self):
        """Update global price dictionary with current and trigger prices"""
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
            
            self.global_price_dict[symbol] = {
                "market_title": market_title,
                "current_price": current_price,
                "trigger_price": trigger_price,
                "price_difference": price_diff,
                "price_difference_percent": price_diff_percent,
                "last_updated": self.prices.get(symbol, {}).get("timestamp"),
                "up_price": None,  # Will be updated by order book processing
                "down_price": None,  # Will be updated by order book processing
                "orderbook_updated": None
            }
        
        # Print the global dictionary
        print("\n=== GLOBAL PRICE DICTIONARY ===")
        for symbol, data in self.global_price_dict.items():
            print(f"{symbol} - {data['market_title']}:")
            print(f"  Current: ${data['current_price']}")
            print(f"  Trigger: ${data['trigger_price']}")
            if data['price_difference'] is not None:
                sign = "+" if data['price_difference'] >= 0 else ""
                print(f"  Difference: {sign}${data['price_difference']:.2f} ({sign}{data['price_difference_percent']:.2f}%)")
            print(f"  Updated: {data['last_updated']}")
            print()
        print("=" * 50)

    def calculate_time_to_end(self, end_date_str):
        """Calculate time remaining until market end"""
        if not end_date_str:
            return "No end date"
            
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            time_diff = end_date - now
            
            if time_diff.total_seconds() < 0:
                return "Market ended"
            
            days = time_diff.days
            hours, remainder = divmod(time_diff.seconds, 3600)
            minutes = remainder // 60
            
            return f"{days}d {hours}h {minutes}m"
        except Exception as e:
            return f"Error: {e}"
            
    async def connect_to_symbol(self, symbol: str):
        """Create a separate WebSocket connection for each symbol with restart capability"""
        while True:
            try:
                await self._connect_websocket(symbol)
            except Exception as e:
                print(f"WebSocket error for {symbol}: {e}")
                await asyncio.sleep(5)
                
    async def _connect_websocket(self, symbol: str):
        """Internal websocket connection method"""
        uri = "wss://ws-live-data.polymarket.com/"
        
        async with websockets.connect(uri) as websocket:
            self.connections[symbol] = websocket
            
            # Send subscription message
            subscription_message = {
                "action": "subscribe",
                "subscriptions": [
                    {
                        "topic": "crypto_prices",
                        "type": "update",
                        "filters": f"{{\"symbol\":\"{symbol}\"}}"
                    }
                ]
            }
            
            await websocket.send(json.dumps(subscription_message))
            
            # Create task for handling messages
            message_task = asyncio.create_task(self._handle_messages(websocket, symbol))
            
            # Create timer task for 5-minute reconnection
            timer_task = asyncio.create_task(asyncio.sleep(500))  # 5 minutes = 300 seconds
            
            try:
                # Wait for either message handling to complete or timer to expire
                done, pending = await asyncio.wait(
                    [message_task, timer_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                
                # Check which task completed
                for task in done:
                    if task == timer_task:
                        print(f"5-minute timer expired for {symbol}, reconnecting...")
                    else:
                        # Message task completed (probably due to error)
                        try:
                            await task  # This will raise the exception if there was one
                        except Exception as e:
                            print(f"Message handling error for {symbol}: {e}")
                            
            except Exception as e:
                print(f"WebSocket connection error for {symbol}: {e}")
            finally:
                # Clean up connection reference
                if symbol in self.connections:
                    del self.connections[symbol]
                    
    async def _handle_messages(self, websocket, symbol: str):
        """Handle incoming WebSocket messages"""
        try:
            async for message in websocket:
                await self.handle_message(message, symbol)
                
                # Check if we need to restart this connection based on update count
                if self.update_counts[symbol] >= 1000:  # Increased threshold since we have 5-min timer
                    print(f"Update count threshold reached for {symbol}, reconnecting...")
                    self.update_counts[symbol] = 0
                    break
        except websockets.exceptions.ConnectionClosed:
            print(f"WebSocket connection closed for {symbol}")
        except Exception as e:
            print(f"Error handling messages for {symbol}: {e}")
            raise
                    
    async def handle_message(self, message: str, symbol: str):
        """Handle incoming WebSocket messages and update prices"""
        try:
            data = json.loads(message)
            if data.get("topic") == "crypto_prices" and data.get("type") == "update":
                payload = data.get("payload", {})
                symbol_from_payload = payload.get("symbol", "").upper()
                price = payload.get("value")
                timestamp = payload.get("timestamp")
                
                # Update prices dictionary
                self.prices[symbol_from_payload] = {
                    "price": price,
                    "timestamp": timestamp
                }
                
                # Increment update count
                self.update_counts[symbol] += 1
                
                # Update global price dictionary
                self.update_global_price_dict()
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Error processing message for {symbol}: {e}")
        
    async def start_tracking(self):
        """Start tracking all crypto symbols with separate connections"""
        # First fetch Polymarket events to update symbols
        await self.fetch_polymarket_events()
        
        # Start order book fetching task
        if self.market_tokens:
            self.order_book_task = asyncio.create_task(self.fetch_order_books())
        
        # Start WebSocket connections with updated symbols
        tasks = []
        for symbol in self.symbols:
            task = asyncio.create_task(self.connect_to_symbol(symbol))
            tasks.append(task)
        
        # Add order book task if it exists
        if self.order_book_task:
            tasks.append(self.order_book_task)
            
        # Run all connections concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

# async def main():
#     tracker = CryptoPriceTracker()
#     try:
#         await tracker.start_tracking()
#     except KeyboardInterrupt:
#         print("\nShutting down...")

# if __name__ == "__main__":
#     asyncio.run(main())