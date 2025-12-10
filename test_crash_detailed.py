#!/usr/bin/env python3
"""
Script to test hypopg crash with detailed logging
"""
import psycopg2
import time
import sys

def test_hypopg_crash():
    try:
        # Connect to database
        conn = psycopg2.connect(
            host='localhost',
            port=15432,
            user='neurdb',
            database='imdb_test'
        )
        conn.autocommit = True
        cursor = conn.cursor()

        print("=== HypoPG Crash Test ===")
        print(f"Connected to database: {conn.info.dbname}")
        print(f"Server version: {conn.info.server_version}")

        # Reset hypopg
        print("\n1. Resetting hypopg...")
        cursor.execute("SELECT hypopg_reset();")
        result = cursor.fetchone()
        print(f"   hypopg_reset() result: {result}")

        # Create hypothetical index
        print("\n2. Creating hypothetical index...")
        cursor.execute("SELECT hypopg_create_index('create index idx_large_test on cast_info(person_id)');")
        result = cursor.fetchone()
        print(f"   hypopg_create_index() result: {result}")

        # Check if index exists
        print("\n3. Checking hypothetical indexes...")
        try:
            cursor.execute("SELECT * FROM hypopg();")
            results = cursor.fetchall()
            print(f"   Found {len(results)} hypothetical indexes:")
            for r in results:
                print(f"     {r}")
        except Exception as e:
            print(f"   Error checking hypopg(): {e}")

        # Now the dangerous EXPLAIN
        print("\n4. Running EXPLAIN (this might crash the server)...")
        try:
            cursor.execute("EXPLAIN (FORMAT JSON) SELECT * FROM cast_info WHERE person_id = 12345;")
            result = cursor.fetchone()
            print("   SUCCESS: EXPLAIN completed without crash!")
            print(f"   Result type: {type(result[0]) if result else 'None'}")
        except psycopg2.OperationalError as e:
            print(f"   CRASH DETECTED: {e}")
            print("   The server connection was lost - this indicates a crash!")
            return False
        except Exception as e:
            print(f"   OTHER ERROR: {e}")
            return False

        # Close connection
        cursor.close()
        conn.close()
        print("\n5. Test completed successfully - no crash detected!")
        return True

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        return False

if __name__ == "__main__":
    success = test_hypopg_crash()
    sys.exit(0 if success else 1)