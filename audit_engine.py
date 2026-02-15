import asyncio
import uvloop
import time
import sys
import os
from pathlib import Path

# 1. SETUP
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
root = Path.cwd().resolve()
sys.path.insert(0, str(root.parent))

from refined_engine.engine import PhemexEngine
from refined_engine.models import Candle

async def run_audit():
    print("[AUDIT] Starting True Performance Profile...")
    
    # 2. BOOT (Measures Parallel I/O and JIT warmup)
    e = PhemexEngine("ETHUSDT")
    await e.boot_async()
    
    # 3. SIMULATE HEAVY LOAD
    print("[AUDIT] Simulating 10s of heavy traffic...")
    start_load = time.time()
    
    # Task A: Rapid Price Ticks (Triggers JIT PnL)
    async def simulate_ticks():
        for i in range(1000):
            # Injecting mock price update
            e._on_price(2000.0 + i, "ETHUSDT")
            if i % 100 == 0: await asyncio.sleep(0.01)

    # Task B: Candle Bursts (Triggers ThreadPool & Amortized Cleanup)
    async def simulate_candles():
        for i in range(5):
            burst = [Candle(time=t, close=2000.0) for t in range(i*1000, (i+1)*1000)]
            e._on_candles(burst)
            await asyncio.sleep(0.5)

    # Task C: Order Pipelining
    async def simulate_orders():
        for _ in range(3):
            # Mocking the network part to measure only our logic overhead
            original_request = e.adapter._request
            e.adapter._request = lambda m, ep, p: {"code": 0, "data": {}}
            await e.limit_buy_batch_async([(0.01, 1900)] * 10)
            e.adapter._request = original_request
            await asyncio.sleep(1)

    await asyncio.gather(simulate_ticks(), simulate_candles(), simulate_orders())
    
    print(f"[AUDIT] Load complete in {time.time() - start_load:.2f}s")
    await e.shutdown_async()

if __name__ == "__main__":
    asyncio.run(run_audit())
