curl -X POST http://localhost:8777/index/auto_create \
  -H "Content-Type: application/json" \
  -d '{
    "workload_queries": [
      "SELECT * FROM cast_info WHERE cast_info.person_id = 12345"
    ],
    "auto_create": true,
    "strategy": "reactive",
    "storage_budget_mb": 10000,
    "enable_cost_comparison": true,
    "database_name": "imdb_test",
    "database_user": "neurdb",
    "database_password": "",
    "enable_hypopg": true,
    "cost_benefit_threshold": 0.1
  }'
