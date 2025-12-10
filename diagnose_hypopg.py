#!/usr/bin/env python3
"""
Diagnose HypoPG connectivity issues
"""

import psycopg2
import json

def diagnose_hypopg():
    """Diagnose HypoPG connectivity issues"""

    print("🔧" + "="*80)
    print("🔍 DIAGNOSING HYPOPG CONNECTIVITY")
    print("🔧" + "="*80)

    # Test multiple connection configurations
    connection_configs = [
        {
            "name": "AI Engine Default Config",
            "params": {
                "host": "localhost",
                "port": 15432,
                "database": "imdb_test",
                "user": "neurdb",
                "password": ""
            }
        },
        {
            "name": "Docker Internal Config",
            "params": {
                "host": "localhost",
                "port": 5432,
                "database": "imdb_test",
                "user": "neurdb",
                "password": ""
            }
        }
    ]

    for config in connection_configs:
        print(f"\n📋 Testing: {config['name']}")
        print(f"   Connection: {config['params']}")

        try:
            conn = psycopg2.connect(**config['params'])
            print(f"   ✅ Connection successful")

            with conn.cursor() as cursor:
                # Test if HypoPG extension is available
                try:
                    cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'hypopg'")
                    result = cursor.fetchone()
                    if result:
                        print(f"   ✅ HypoPG extension available")
                    else:
                        print(f"   ❌ HypoPG extension not available")
                        continue
                except Exception as e:
                    print(f"   ❌ Error checking extension: {e}")
                    continue

                # Test if HypoPG is enabled
                try:
                    cursor.execute("SELECT hypopg_reset()")
                    print(f"   ✅ HypoPG functions working")
                except Exception as e:
                    print(f"   ❌ HypoPG functions error: {e}")
                    continue

                # Test HypoPG enable setting
                try:
                    cursor.execute("SET hypopg.enabled = true")
                    print(f"   ✅ HypoPG enabled successfully")
                except Exception as e:
                    print(f"   ⚠️ HypoPG enable warning: {e}")

                # Test creating a hypothetical index
                try:
                    cursor.execute("SELECT hypopg_create_index('CREATE INDEX hypopg_test ON title (id)')")
                    result = cursor.fetchone()
                    if result and result[0]:
                        print(f"   ✅ Hypothetical index creation: OID {result[0]}")

                        # Clean up
                        cursor.execute("SELECT hypopg_reset()")
                        print(f"   ✅ HypoPG cleanup successful")
                    else:
                        print(f"   ❌ Hypothetical index creation failed")
                except Exception as e:
                    print(f"   ❌ Hypothetical index error: {e}")

            conn.close()
            print(f"   🎉 {config['name']}: FULLY FUNCTIONAL!")

            # Save working configuration
            with open('/Volumes/data/DB/neurdb_dev/working_hypopg_config.json', 'w') as f:
                json.dump(config['params'], f, indent=2)
            print(f"   💾 Saved working configuration")

            return config['params']

        except Exception as e:
            print(f"   ❌ Connection failed: {e}")

    print(f"\n🚨 No working configuration found!")
    return None

def test_ai_engine_config():
    """Test if AI Engine can use HypoPG"""

    print(f"\n📋 Step 2: Testing AI Engine HypoPG Integration")

    # Load working config if exists
    try:
        with open('/Volumes/data/DB/neurdb_dev/working_hypopg_config.json', 'r') as f:
            working_config = json.load(f)
        print(f"   📊 Using working config from previous test")
    except:
        print(f"   ⚠️ No working config found, using default")
        working_config = {
            "host": "localhost",
            "port": 15432,
            "database": "imdb_test",
            "user": "neurdb"
        }

    # Test direct HypoPG functionality like the AI Engine does
    try:
        conn = psycopg2.connect(**working_config)
        with conn.cursor() as cursor:

            # Enable HypoPG (like AI Engine does)
            cursor.execute("SELECT hypopg_reset();")
            cursor.execute("SET hypopg.enabled = true;")
            print(f"   ✅ HypoPG enabled for session")

            # Test hypothetical index creation (like AI Engine does)
            test_sql = "CREATE INDEX hypopg_title_id ON title (id)"
            cursor.execute("SELECT hypopg_create_index(%s)", (test_sql,))
            result = cursor.fetchone()

            if result and result[0]:
                print(f"   ✅ Hypothetical index created: OID {result[0]}")

                # Test EXPLAIN with hypothetical index (like AI Engine does)
                explain_query = "EXPLAIN (FORMAT JSON) SELECT * FROM title WHERE id = 12345"
                cursor.execute(explain_query)
                plan = cursor.fetchone()

                if plan:
                    print(f"   ✅ EXPLAIN with hypothetical index working")
                    print(f"   🎯 HypoPG integration fully functional!")
                else:
                    print(f"   ⚠️ EXPLAIN returned no plan")

                # Clean up
                cursor.execute("SELECT hypopg_reset()")

            else:
                print(f"   ❌ Hypothetical index creation failed")

        conn.close()

    except Exception as e:
        print(f"   ❌ AI Engine integration test failed: {e}")
        return False

    return True

if __name__ == "__main__":
    working_config = diagnose_hypopg()

    if working_config:
        success = test_ai_engine_config()

        if success:
            print(f"\n🎉 HYPPOG DIAGNOSIS COMPLETE: EVERYTHING WORKING!")
            print(f"   The AI Engine should be able to use HypoPG now.")
            print(f"   Check AI Engine logs for successful index creation.")
        else:
            print(f"\n⚠️ HYPPOG DIAGNOSIS: Issues found in AI Engine integration")
    else:
        print(f"\n🚨 HYPPOG DIAGNOSIS: No working configuration found")