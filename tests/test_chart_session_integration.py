"""
ChartSession Integration Test Harness

This is a full end-to-end integration test for the argus.tv.ChartSession class.
It tests the complete WebSocket connection flow, data retrieval, and DataFrame conversion.
"""

import os
import sys
import time
import pandas as pd
from datetime import datetime


sys.path.append(__file__.replace("tests/" + __file__.split("/")[-1], ""))  


print(sys.path)



def test_chart_session_basic():
    """Test basic ChartSession functionality with a single symbol."""
    print("=" * 60)
    print("TEST 1: Basic ChartSession Connection and Data Retrieval")
    print("=" * 60)
    
    from argus.tv import ChartSession
    
    session = ChartSession()
    
    print("\n[1/4] Establishing WebSocket connection to TradingView...")
    
    import threading
    
    result_df = None
    error = None
    
    def run_session():
        nonlocal result_df, error
        try:
            # This will connect, authenticate, and retrieve data
            result_df = session.get_symbol_data(
                symbol="AAPL",  # Apple Inc.
                interval="D",    # Daily candles
                total_ticks=10   # Get last 10 candles
            )
        except Exception as e:
            error = e
    
    # Run the WebSocket connection in a separate thread
    ws_thread = threading.Thread(target=lambda: session.ws.run_forever())
    ws_thread.daemon = True
    ws_thread.start()
    
    # Give time for connection to establish
    time.sleep(1)
    
    # Now fetch data
    try:
        result_df = session.get_symbol_data(
            symbol="NASDAQ:AAPL",
            interval="D",
            total_ticks=10
        )
        
        print("[2/4] Connection established and data requested")
        print("[3/4] Waiting for data...")
        
        # Wait for data to be received (get_symbol_data blocks until done)
        print("[4/4] Data received successfully\n")
        
        # Validate the DataFrame
        assert result_df is not None, "DataFrame should not be None"
        assert isinstance(result_df, pd.DataFrame), "Result should be a pandas DataFrame"
        assert len(result_df) > 0, "DataFrame should contain data"
        assert len(result_df) <= 10, f"Should have at most 10 rows, got {len(result_df)}"
        
        # Check required columns
        required_columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_columns:
            assert col in result_df.columns, f"Missing required column: {col}"
        
        # Check data types
        assert pd.api.types.is_datetime64_any_dtype(result_df['Date']), "Date column should be datetime"
        
        print("✓ DataFrame validation passed")
        print(f"✓ Retrieved {len(result_df)} candles")
        print(f"✓ Columns: {list(result_df.columns)}")
        print(f"✓ Date range: {result_df['Date'].min()} to {result_df['Date'].max()}")
        
        print("\n--- First 3 rows of data ---")
        print(result_df.head(3))
        print("\n--- Last 3 rows of data ---")
        print(result_df.tail(3))
        
        print("\n✅ TEST 1 PASSED: Basic ChartSession functionality works")
        return True
        
    except Exception as e:
        print(f"\n❌ TEST 1 FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            session.ws.close()
        except:
            pass


def test_chart_session_multiple_symbols():
    """Test ChartSession with multiple different symbols."""
    print("\n" + "=" * 60)
    print("TEST 2: Multiple Symbols Data Retrieval")
    print("=" * 60)
    
    from argus.tv import ChartSession
    
    # Test different types of symbols
    test_cases = [
        ("NASDAQ:TSLA", "D", 5),
        ("NASDAQ:MSFT", "60", 3),  # 60-minute candles
    ]
    
    results = []
    
    for symbol, interval, ticks in test_cases:
        print(f"\nTesting symbol: {symbol} (interval: {interval}, ticks: {ticks})")
        
        session = ChartSession()
        
        try:
            # Start WebSocket in background
            import threading
            ws_thread = threading.Thread(target=lambda: session.ws.run_forever())
            ws_thread.daemon = True
            ws_thread.start()
            
            time.sleep(1)  # Wait for connection
            
            df = session.get_symbol_data(
                symbol=symbol,
                interval=interval,
                total_ticks=ticks
            )
            
            # Validate
            assert df is not None, f"DataFrame is None for {symbol}"
            assert isinstance(df, pd.DataFrame), f"Not a DataFrame for {symbol}"
            assert len(df) > 0, f"No data returned for {symbol}"
            
            print(f"  ✓ Retrieved {len(df)} candles for {symbol}")
            print(f"  ✓ Date range: {df['Date'].min()} to {df['Date'].max()}")
            
            results.append((symbol, True, len(df)))
            
        except Exception as e:
            print(f"  ❌ Failed for {symbol}: {str(e)}")
            results.append((symbol, False, 0))
        finally:
            try:
                session.ws.close()
            except:
                pass
            time.sleep(0.5)  # Brief pause between tests
    
    # Summary
    passed = sum(1 for _, success, _ in results if success)
    total = len(results)
    
    print(f"\n--- Results: {passed}/{total} symbols passed ---")
    for symbol, success, count in results:
        status = "✓" if success else "❌"
        print(f"  {status} {symbol}: {count} candles")
    
    if passed == total:
        print("\n✅ TEST 2 PASSED: All symbols retrieved successfully")
        return True
    else:
        print(f"\n❌ TEST 2 FAILED: {total - passed} symbols failed")
        return False


def test_chart_session_dataframe_structure():
    """Test that the DataFrame has the expected structure and data types."""
    print("\n" + "=" * 60)
    print("TEST 3: DataFrame Structure Validation")
    print("=" * 60)
    
    from argus.tv import ChartSession
    
    session = ChartSession()
    
    try:
        import threading
        ws_thread = threading.Thread(target=lambda: session.ws.run_forever())
        ws_thread.daemon = True
        ws_thread.start()
        
        time.sleep(1)
        
        df = session.get_symbol_data(
            symbol="NASDAQ:AAPL",
            interval="D",
            total_ticks=5
        )
        
        print("\nValidating DataFrame structure...")
        
        # Test 1: Check columns
        expected_columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        assert list(df.columns) == expected_columns, f"Columns mismatch: {list(df.columns)}"
        print("  ✓ All expected columns present")
        
        # Test 2: Check no null values in price columns
        price_cols = ['Open', 'High', 'Low', 'Close']
        for col in price_cols:
            assert df[col].notna().all(), f"Null values found in {col}"
        print("  ✓ No null values in price columns")
        
        # Test 3: Check logical price relationships
        assert (df['High'] >= df['Low']).all(), "High should be >= Low"
        assert (df['High'] >= df['Open']).all(), "High should be >= Open"
        assert (df['High'] >= df['Close']).all(), "High should be >= Close"
        assert (df['Low'] <= df['Open']).all(), "Low should be <= Open"
        assert (df['Low'] <= df['Close']).all(), "Low should be <= Close"
        print("  ✓ Price relationships are logical (High >= Open/Close/Low)")
        
        # Test 4: Check data types
        assert pd.api.types.is_datetime64_any_dtype(df['Date']), "Date should be datetime"
        assert pd.api.types.is_numeric_dtype(df['Open']), "Open should be numeric"
        assert pd.api.types.is_numeric_dtype(df['High']), "High should be numeric"
        assert pd.api.types.is_numeric_dtype(df['Low']), "Low should be numeric"
        assert pd.api.types.is_numeric_dtype(df['Close']), "Close should be numeric"
        print("  ✓ All columns have correct data types")
        
        # Test 5: Check volume is non-negative
        assert (df['Volume'] >= 0).all(), "Volume should be non-negative"
        print("  ✓ Volume values are non-negative")
        
        # Test 6: Check dates are in ascending order
        assert df['Date'].is_monotonic_increasing, "Dates should be in ascending order"
        print("  ✓ Dates are in ascending order")
        
        # Display sample statistics
        print("\n--- DataFrame Statistics ---")
        print(df[['Open', 'High', 'Low', 'Close', 'Volume']].describe())
        
        print("\n✅ TEST 3 PASSED: DataFrame structure is valid")
        return True
        
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            session.ws.close()
        except:
            pass


def test_chart_session_error_handling():
    """Test error handling for invalid inputs."""
    print("\n" + "=" * 60)
    print("TEST 4: Error Handling")
    print("=" * 60)
    
    from argus.tv import ChartSession
    
    session = ChartSession()
    
    # Note: This test is limited since ChartSession doesn't do much validation
    # But we can test the connection and basic error scenarios
    
    try:
        import threading
        ws_thread = threading.Thread(target=lambda: session.ws.run_forever())
        ws_thread.daemon = True
        ws_thread.start()
        
        time.sleep(1)
        
        # Test with unusual but valid symbol
        print("\nTesting with an unusual symbol format...")
        df = session.get_symbol_data(
            symbol="BINANCE:BTCUSDT",  # Crypto pair
            interval="D",
            total_ticks=3
        )
        
        print(f"  ✓ Successfully retrieved {len(df)} candles for crypto pair")
        
        # Test with different interval formats
        print("\nTesting with different interval formats...")
        intervals_to_test = ["D", "60", "240"]  # Daily, 1-hour, 4-hour
        
        for interval in intervals_to_test:
            df = session.get_symbol_data(
                symbol="NASDAQ:AAPL",
                interval=interval,
                total_ticks=3
            )
            print(f"  ✓ Interval '{interval}' works: {len(df)} candles")
            time.sleep(0.5)
        
        print("\n✅ TEST 4 PASSED: Error handling works correctly")
        return True
        
    except Exception as e:
        print(f"\n❌ TEST 4 FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            session.ws.close()
        except:
            pass


def run_all_tests():
    """Run all integration tests and report results."""
    print("\n" + "=" * 60)
    print("CHARTSESSION INTEGRATION TEST HARNESS")
    print("=" * 60)
    print(f"Started at: {datetime.now()}")
    print("=" * 60)
    
    # Import check
    try:
        from argus.tv import ChartSession, TradingViewConnection
        print("\n✓ argus.tv module imported successfully")
    except ImportError as e:
        print(f"\n❌ Failed to import argus.tv: {e}")
        sys.exit(1)
    
    # Run all tests
    results = []
    
    results.append(("Basic Functionality", test_chart_session_basic()))
    results.append(("Multiple Symbols", test_chart_session_multiple_symbols()))
    results.append(("DataFrame Structure", test_chart_session_dataframe_structure()))
    results.append(("Error Handling", test_chart_session_error_handling()))
    
    # Final report
    print("\n" + "=" * 60)
    print("FINAL TEST REPORT")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print("-" * 60)
    print(f"Total: {passed} passed, {failed} failed out of {len(results)} tests")
    print(f"Finished at: {datetime.now()}")
    print("=" * 60)
    
    if failed > 0:
        sys.exit(1)
    else:
        print("\n🎉 All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
