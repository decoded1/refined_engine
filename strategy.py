import time
import signal
import sys
from refined_engine import PhemexEngine

# Global flag for clean shutdown
RUNNING = True

def handle_exit(signum, frame):
    global RUNNING
    print("\n[STOP] Signal received. Shutting down...")
    RUNNING = False

def run_strategy():
    # 1. Initialize
    symbol = "BTCUSDT"
    print(f"--- Strategy Starting ({symbol}) ---")
    
    engine = PhemexEngine(symbol=symbol)
    engine.boot()
    
    # Register signal handlers (Ctrl+C)
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    print(f"Price: ${engine.price:,.2f}")
    
    # 2. Main Loop
    while RUNNING:
        try:
            # A. Update Data
            price = engine.price
            # Filter for OUR active positions (ignoring any ghost data if possible)
            position = next((p for p in engine.positions if p.size != 0), None)
            
            # B. Trading Logic
            if not position:
                # Simple Logic: Place a Buy limit $50 below market
                target = price - 50
                qty = 0.002 # Engine will auto-truncate this!
                
                # Check if we already have an open order to avoid spamming
                open_buys = [o for o in engine.orders if o.side == "Buy"]
                
                if not open_buys:
                    print(f"📉 No position. Placing Buy: {qty} @ {target:.2f}")
                    # Note: Using 'Long' for Hedge Mode compatibility
                    engine.limit_buy(qty, target, pos_side="Long")
                else:
                    # Move order if price moved away? (Simple market making)
                    pass
                    
            else:
                # We have a position
                pnl = position.unrealized_pnl
                print(f"📈 Position: {position.side} {position.size} | PnL: ${pnl:.2f}")

                # Take Profit (+ $5)
                if pnl > 5.0:
                    print("💰 Take Profit hit! Closing...")
                    engine.market_sell(position.size, pos_side="Long")
                
                # Stop Loss (- $2)
                elif pnl < -2.0:
                    print("🛑 Stop Loss hit! Closing...")
                    engine.market_sell(position.size, pos_side="Long")

            time.sleep(1)

        except Exception as e:
            print(f"Error in loop: {e}")
            time.sleep(1)

    # 3. Shutdown Sequence
    print("Canceling open orders...")
    try:
        # Cancel only our active side
        engine.cancel_all(pos_side="Long") 
    except:
        pass
        
    engine.shutdown()

if __name__ == "__main__":
    run_strategy()