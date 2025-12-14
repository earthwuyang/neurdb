# Reactive Index Management Architecture

## Overview

Reactive Index Management in NeurDB provides automatic, on-demand index creation for queries executed by users. Unlike predictive index management which analyzes workload patterns, reactive management intercepts individual queries and creates indexes in real-time based on the current query execution needs.

## Architecture Components

### 1. Index Management Extension (`nr_index_management`)

**Location**: `/dbengine/nr_kernel/nr_index_management/`

**Responsibilities**:
- Intercepts all SELECT queries when `nr_index_management_strategy = 'reactive'`
- Sends intercepted queries to AI engine for analysis
- Receives and executes index creation commands from AI engine
- Manages storage budget constraints for automatic index creation

**Key Functions**:
- `index_management_post_parse_analyze()` - Query interception hook
- `send_query_to_ai_engine()` - Sends queries to AI engine
- `execute_index_creation_commands()` - Executes AI-recommended indexes

### 2. AI Engine (`reactive_index_manager.py`)

**Location**: `/aiengine/workload_forecast/src/reactive_index_manager.py`

**Responsibilities**:
- Receives intercepted queries from index_management extension
- Analyzes queries for optimal index opportunities
- Checks `nr_enable_auto_index_creation` flag before creating indexes
- Sends index creation commands back to database when auto-creation is enabled
- Respects storage budget constraints

**Key Methods**:
- `process_reactive_query()` - Main query processing logic
- `analyze_query_for_indexes()` - Query analysis and recommendation
- `create_indexes_if_enabled()` - Index creation with auto-creation check

### 3. Workload Forecast Extension (`nr_workload_forecast`)

**Location**: `/dbengine/nr_kernel/nr_workload_forecast/`

**Responsibilities**:
- Only active when `nr_index_management_strategy = 'predictive'`
- Logs queries for workload analysis and prediction
- Does not interfere with reactive processing
- Maintains historical query patterns for future predictions

## Configuration Parameters

### Strategy Selection
```sql
-- Enable reactive index management (automatic creation)
SET nr_index_management_strategy = 'reactive';

-- Enable predictive index management (analysis only)
SET nr_index_management_strategy = 'predictive';
```

### Reactive Controls
```sql
-- Enable automatic index creation by AI engine
SET nr_enable_auto_index_creation = true;

-- Disable automatic creation (AI will analyze but not create)
SET nr_enable_auto_index_creation = false;
```

### Storage Budget
```sql
-- Set maximum storage for auto-created indexes
SET nr_max_index_storage_mb = 1000.0;
```

### Workload Forecast (predictive mode)
```sql
-- Enable workload forecast logging
SET workload_forecast.enable = true;
```

## Query Flow

### Reactive Mode (`nr_index_management_strategy = 'reactive'`)

1. **Query Interception**: `index_management_post_parse_analyze()` hook is called for every SELECT query
2. **Strategy Check**: Verifies strategy is 'reactive'
3. **Query Transmission**: Sends query to AI engine at `/index/reactive/manage` endpoint
4. **AI Analysis**: AI engine analyzes query for index opportunities
5. **Auto-Creation Check**: AI engine checks `nr_enable_auto_index_creation` flag
6. **Index Creation**: If enabled, AI sends index creation commands back to database
7. **Budget Enforcement**: Index creation respects storage budget constraints

### Predictive Mode (`nr_index_management_strategy = 'predictive'`)

1. **Query Logging**: `workload_forecast_post_parse_analyze()` logs queries
2. **Workload Analysis**: Queries stored for pattern analysis
3. **Periodic Recommendations**: AI engine generates index recommendations based on patterns
4. **Manual Review**: Administrators review and manually create recommended indexes

## Implementation Details

### Query Interception Hook (index_management)

```c
void index_management_post_parse_analyze(ParseState *pstate, Query *query, JumbleState *jstate)
{
    // Only process SELECT queries
    if (query->commandType != CMD_SELECT)
        return;

    // Check if reactive strategy is enabled
    if (strcmp(nr_index_management_strategy, "reactive") != 0)
        return;

    // Send query to AI engine for analysis
    send_query_to_ai_engine(query_text);
}
```

### AI Engine Processing Logic

```python
async def process_reactive_query(self, query_text: str) -> Dict:
    # Analyze query for index opportunities
    recommendations = await self.analyze_query_for_indexes(query_text)

    # Check if auto-creation is enabled
    if not nr_enable_auto_index_creation:
        return {
            "status": "analysis_complete",
            "recommendations": recommendations,
            "auto_creation_disabled": True
        }

    # Create indexes if budget allows
    if recommendations and self.has_budget_space():
        await self.create_recommended_indexes(recommendations)

    return {"status": "index_creation_completed"}
```

### Storage Budget Management

- Index creation checks available storage before proceeding
- When budget is exhausted, new index creation is blocked
- Existing indexes can be evicted based on usage patterns
- Budget is calculated as `nr_max_index_storage_mb` or 50% of database size

## Error Handling

### Index Management Extension
- Graceful degradation if AI engine is unavailable
- Queries continue execution regardless of AI engine response
- Connection timeout and retry logic for AI engine communication
- Comprehensive logging of all operations

### AI Engine
- Validates index creation commands before execution
- Handles database connection failures gracefully
- Respects transaction boundaries and constraints
- Provides detailed error responses to database

## Performance Considerations

### Query Overhead
- Non-blocking design ensures minimal impact on query performance
- AI engine communication is asynchronous where possible
- Query interception adds minimal parsing overhead
- No query rewriting or modification occurs

### Resource Usage
- Index creation respects I/O and CPU resource limits
- Background index creation prevents query blocking
- Memory usage is bounded during index creation
- Storage budget enforcement prevents disk exhaustion

## Monitoring and Logging

### Database Logs
All reactive operations are logged with prefixes:
- `REACTIVE INDEX:` - Query interception and AI engine communication
- `REACTIVE CREATION:` - Index creation operations
- `REACTIVE BUDGET:` - Storage budget management

### AI Engine Logs
- Query analysis processing times
- Index creation success/failure rates
- Storage budget status
- Performance metrics and recommendations

## Security Considerations

### Query Security
- Only SELECT queries are intercepted (no DML or DDL)
- Query text is sanitized before AI engine transmission
- No sensitive data is exposed to external systems beyond query patterns

### Index Creation Security
- Only indexes on user tables are allowed
- System catalog queries are ignored
- Index creation follows standard PostgreSQL security model
- All indexes are created with appropriate permissions

## Testing Strategy

### Unit Tests
- Query interception with various SQL constructs
- AI engine communication protocols
- Budget enforcement logic
- Error handling scenarios

### Integration Tests
- End-to-end reactive index creation workflow
- Predictive vs reactive mode isolation
- Storage budget management
- Performance impact measurements

### Load Tests
- High-throughput query processing
- Concurrent index creation
- Resource utilization under load
- Scalability limits and bottlenecks

## Future Enhancements

### Query Optimization
- Query similarity detection to avoid redundant AI analysis
- Intelligent query filtering for better performance
- Caching of AI engine responses for similar queries
- Adaptive query sampling based on system load

### Index Management
- Smart index eviction policies
- Index usage tracking and statistics
- Automatic index consolidation
- Multi-column index optimization

### AI Engine Enhancements
- Machine learning for better index recommendations
- Cost-benefit analysis for index creation decisions
- Workload-aware index selection
- Integration with database statistics and histograms