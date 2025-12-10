#!/usr/bin/env python3
"""
Diagnose the GUC parameter reading issue
"""

import subprocess

def diagnose_guc_issue():
    """Diagnose why GUC parameter isn't being read correctly"""

    print("🔧" + "="*80)
    print("🔍 DIAGNOSING GUC PARAMETER READING ISSUE")
    print("🔧" + "="*80)

    # Test 1: Check what PostgreSQL thinks the GUC value is
    print(f"\n📋 Step 1: Check PostgreSQL GUC value directly")

    try:
        result = subprocess.run([
            'docker', 'exec', 'neurdb_dev', 'bash', '-c',
            'cd /code/neurdb-dev/psql && ./bin/psql -U neurdb -d imdb_test -t -c "SHOW nr_max_index_storage_mb;"'
        ], capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            postgres_guc = result.stdout.strip()
            print(f"   PostgreSQL GUC value: {postgres_guc}")
        else:
            print(f"   Error checking PostgreSQL GUC: {result.stderr}")
    except Exception as e:
        print(f"   Exception: {e}")

    # Test 2: Check via current_setting()
    print(f"\n📋 Step 2: Check via current_setting()")

    try:
        result = subprocess.run([
            'docker', 'exec', 'neurdb_dev', 'bash', '-c',
            'cd /code/neurdb-dev/psql && ./bin/psql -U neurdb -d imdb_test -t -c "SELECT current_setting(\'nr_max_index_storage_mb\');"'
        ], capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            current_setting = result.stdout.strip()
            print(f"   current_setting() value: {current_setting}")
        else:
            print(f"   Error checking current_setting: {result.stderr}")
    except Exception as e:
        print(f"   Exception: {e}")

    # Test 3: Check what the extension function returns
    print(f"\n📋 Step 3: Check extension function")

    try:
        result = subprocess.run([
            'docker', 'exec', 'neurdb_dev', 'bash', '-c',
            'cd /code/neurdb-dev/psql && ./bin/psql -U neurdb -d imdb_test -t -c "SELECT nr_calculate_index_budget_mb();"'
        ], capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            extension_result = result.stdout.strip()
            print(f"   Extension function result: {extension_result}")
        else:
            print(f"   Error checking extension function: {result.stderr}")
    except Exception as e:
        print(f"   Exception: {e}")

    # Test 4: Try the SQL fallback from our updated function
    print(f"\n📋 Step 4: Test SQL fallback (what our updated code should use)")

    try:
        result = subprocess.run([
            'docker', 'exec', 'neurdb_dev', 'bash', '-c',
            'cd /code/neurdb-dev/psql && ./bin/psql -U neurdb -d imdb_test -t -c "SELECT current_setting(\'nr_max_index_storage_mb\')::double precision;"'
        ], capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            sql_result = result.stdout.strip()
            print(f"   SQL fallback result: {sql_result}")
        else:
            print(f"   Error checking SQL fallback: {result.stderr}")
    except Exception as e:
        print(f"   Exception: {e}")

    print(f"\n🎯 Step 5: Analysis and Solution")

    try:
        # Convert to float for comparison
        postgres_guc = float(postgres_guc) if 'postgres_guc' in locals() else 0
        extension_result = float(extension_result) if 'extension_result' in locals() else 0
        sql_result = float(sql_result) if 'sql_result' in locals() else 0

        print(f"   📊 Values comparison:")
        print(f"      PostgreSQL SHOW: {postgres_guc}")
        print(f"      current_setting(): {sql_result}")
        print(f"      Extension function: {extension_result}")

        if postgres_guc == 10000 and extension_result != 10000:
            print(f"\n   🔍 DIAGNOSIS:")
            print(f"      ✅ GUC parameter is set correctly in PostgreSQL ({postgres_guc})")
            print(f"      ❌ Extension function is not reading it ({extension_result})")
            print(f"      🔧 The issue is in the C extension implementation")

            print(f"\n   💡 SOLUTION:")
            print(f"      The global variable nr_max_index_storage_mb is not being updated")
            print(f"      Need to fix the C extension to read GUC properly")
            print(f"      The SQL fallback should work: current_setting() = {sql_result}")

        elif postgres_guc != 10000:
            print(f"\n   🔍 DIAGNOSIS:")
            print(f"      ❌ GUC parameter not set properly in PostgreSQL")
            print(f"      Expected: 10000, Got: {postgres_guc}")
            print(f"      🔧 Need to set: SET nr_max_index_storage_mb = 10000;")

        else:
            print(f"\n   🔍 DIAGNOSIS:")
            print(f"      ✅ All values match - something else is the issue")

    except Exception as e:
        print(f"   Error in comparison: {e}")

    print(f"\n🎯 NEXT STEPS:")
    print(f"   1. The C extension needs to use the SQL fallback method")
    print(f"   2. Or fix the global variable update mechanism")
    print(f"   3. Test with the working current_setting() approach")

if __name__ == "__main__":
    diagnose_guc_issue()