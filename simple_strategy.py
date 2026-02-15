import time
from refined_engine import PhemexEngine

def run_strategy():
    # 1. Initialize
    symbol = "BTCUSDT"
    engine = PhemexEngine(symbol=symbol)
    engine.boot()

    print(f"--- Started Strategy on {symbol} ---")
    print(f"Price: ${engine.price:,.2f}")
    
    try:
        while True:
            # 2. Update logic (The Engine keeps data fresh in background)
            current_price = engine.price
            position = next((p for p in engine.positions if p.size != 0), None)

            # 3. Trading Logic
            if not position:
                # No position? Place a Limit Buy slightly below market
                target_price = current_price - 100
                qty = 0.005 # Engine will auto-truncate this if you mess up!
                
                print(f"No position. Placing Buy Order @ {target_price}")
                engine.limit_buy(qty, target_price)
                
                # Wait for fill (simple demo logic)
                time.sleep(5) 
            
            else:
                # Have position? Check PnL
                pnl = position.unrealized_pnl
                print(f"Position: {position.side} {position.size} BTC | PnL: ${pnl:.2f}")

                # Take Profit (+ $10) or Stop Loss (- $5)
                if pnl > 10 or pnl < -5:
                    print("Closing position...")
                    engine.market_sell(position.size) # Closes a Long
                    # OR engine.cancel_all() to kill open orders

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
        engine.cancel_all() # Safety cleanup
        engine.shutdown()

if __name__ == "__main__":
    run_strategy()