# Index Management Strategies Implementation Plan

## Overview
Implementation of two complementary index management strategies for NeurDB:
- **Predictive Strategy**: Proactive index management based on workload forecasting and clustering
- **Reactive Strategy**: Real-time index management responding to incoming queries

## Goals
1. Minimize query execution time through optimal index selection
2. Manage storage budget efficiently
3. Reduce manual index management overhead
4. Provide both proactive and reactive optimization approaches

## Architecture Overview

### Predictive Strategy Components
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Workload        │    │  Forecasting     │    │  Cost-Based     │
│  Collector       │───▶│  Pipeline        │───▶│  Index Advisor  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Workload        │    │  Change          │    │  Index          │
│  Clustering      │    │  Detection       │    │  Recreation     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Index Management Decisions                     │
└─────────────────────────────────────────────────────────────────┘
```

### Reactive Strategy Components
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Incoming        │    │  Query           │    │  Benefit        │
│  Query           │───▶│  Analysis        │───▶│  Calculation    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Index           │    │  Budget          │    │  Index          │
│  Recommendation  │───▶│  Check           │───▶│  Creation       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Eviction Logic (if needed)                       │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Components

### 1. Predictive Index Manager
**File**: `aiengine/workload_forecast/src/predictive_index_manager.py`

**Features**:
- Continuous workload monitoring (5-minute intervals)
- Workload forecasting (24-hour horizon)
- Workload clustering and pattern detection
- Change detection algorithms
- DTA-inspired cost-based optimization
- Complete index recreation when significant changes detected
- Storage budget management

**Key Classes**:
- `PredictiveIndexManager`: Main orchestrator
- `WorkloadChange`: Represents detected workload changes
- `WorkloadChangeType`: Enum of change types
- `PredictiveConfig`: Configuration object

### 2. Reactive Index Manager
**File**: `aiengine/workload_forecast/src/reactive_index_manager.py`

**Features**:
- Real-time query analysis
- Per-query index recommendation
- Benefit calculation using HypoPG
- Budget-aware index creation
- LRU/MFU-based eviction policies
- Query cost optimization
- Immediate response to workload changes

**Key Classes**:
- `ReactiveIndexManager`: Main reactive manager
- `IndexBenefit`: Represents index benefit calculation
- `EvictionPolicy`: Base class for eviction strategies
- `BudgetManager`: Manages storage budget and allocation

### 3. Enhanced Database Functions
**Files**:
- `dbengine/nr_kernel/nr_workload_forecast/nr_workload_forecast.c`
- `dbengine/nr_kernel/nr_workload_forecast/nr_workload_forecast--1.0.sql`

**New Functions**:
- `nr_get_index_benefit(index_name, query_text)`: Calculate benefit for specific query
- `nr_evict_least_useful_index(required_space_mb)`: Evict least useful index
- `nr_get_query_index_requirements(query_text)`: Analyze query index needs
- `nr_calculate_index_creation_cost(columns[], index_type)`: Estimate creation cost

### 4. API Endpoints
**File**: `aiengine/workload_forecast/run_server.py`

**New Endpoints**:
- `POST /index/predictive/optimize`: Trigger predictive optimization
- `GET /index/predictive/status`: Get predictive manager status
- `POST /index/reactive/recommend`: Get recommendation for single query
- `POST /index/reactive/manage`: Create index with budget management
- `GET /index/reactive/status`: Get reactive manager status
- `POST /index/switch_strategy`: Switch between strategies

## Implementation Details

### Predictive Strategy Algorithm

1. **Workload Collection** (every 5 minutes):
   ```sql
   SELECT query, calls, total_exec_time, mean_exec_time, rows
   FROM pg_stat_statements
   WHERE calls > 10
   ORDER BY total_exec_time DESC
   ```

2. **Change Detection**:
   - Frequency change detection: |new_freq - old_freq| / old_freq > threshold
   - Performance change detection: |new_time - old_time| / old_time > threshold
   - Pattern analysis using clustering algorithms
   - Forecast variance analysis

3. **Reoptimization Trigger**:
   - Significant workload change detected
   - Forecast predicts major changes
   - Time since last optimization > 24 hours
   - Performance degradation > 20%

4. **Index Recreation Process**:
   - Delete all auto-managed indexes (idx_auto_*)
   - Run cost-based optimizer on workload
   - Select top-k beneficial indexes within budget
   - Create new indexes using `nr_create_index_if_budget_allows()`

### Reactive Strategy Algorithm

1. **Query Analysis**:
   ```python
   # Parse query to extract table references and predicates
   # Generate potential index candidates
   # Calculate benefit using HypoPG cost estimates
   ```

2. **Benefit Calculation**:
   ```python
   benefit = (baseline_cost - index_cost) / baseline_cost
   if benefit > min_benefit_threshold:
       recommend_index()
   ```

3. **Budget Management**:
   ```python
   if required_space > available_budget:
       evict_index(find_least_useful_index())
       create_new_index()
   ```

4. **Eviction Policies**:
   - **LRU**: Evict least recently used index
   - **MFU**: Evict most frequently used index (wrong - should be least)
   - **Benefit-based**: Evict index with lowest benefit/cost ratio
   - **Cost-based**: Evict index with highest maintenance cost

### Storage Budget Management

1. **Budget Calculation**:
   ```sql
   CREATE FUNCTION nr_calculate_index_budget_mb()
   RETURNS float AS $$
   BEGIN
     RETURN (SELECT pg_database_size(current_database()) * 0.02); -- 2% of DB size
   END;
   $$ LANGUAGE plpgsql;
   ```

2. **Usage Tracking**:
   ```sql
   CREATE FUNCTION nr_get_current_index_storage_mb()
   RETURNS float AS $$
   BEGIN
     RETURN (
       SELECT COALESCE(SUM(pg_relation_size(indexrelid)), 0) / 1024.0 / 1024.0
       FROM pg_index
       JOIN pg_class ON pg_class.oid = pg_index.indexrelid
       WHERE indexname LIKE 'idx_auto_%'
     );
   END;
   $$ LANGUAGE plpgsql;
   ```

## Configuration

### Predictive Strategy Config
```python
predictive_config = {
    'forecast_horizon_hours': 24,
    'min_confidence_for_action': 0.75,
    'workload_change_threshold': 0.3,
    'enable_index_recreation': True,
    'storage_budget_mb': 1000.0,
    'max_indexes_to_create': 10,
    'min_index_benefit_threshold': 0.1,
    'analysis_window_hours': 24,
    'monitoring_interval_minutes': 5
}
```

### Reactive Strategy Config
```python
reactive_config = {
    'storage_budget_mb': 1000.0,
    'min_benefit_threshold': 0.1,
    'eviction_policy': 'benefit_based',  # lru, mfu, benefit_based, cost_based
    'max_indexes_total': 50,
    'cache_recommendations': True,
    'recommendation_ttl_hours': 1,
    'enable_auto_creation': True
}
```

## Testing Strategy

### Unit Tests
1. **Predictive Manager Tests**:
   - Workload change detection accuracy
   - Forecast integration
   - Index recreation scenarios
   - Budget management

2. **Reactive Manager Tests**:
   - Single query recommendation accuracy
   - Benefit calculation correctness
   - Eviction policy effectiveness
   - Budget constraint handling

### Integration Tests
1. **End-to-End Workflows**:
   - Predictive strategy full cycle
   - Reactive strategy real-time operation
   - Strategy switching scenarios

2. **Performance Tests**:
   - Query execution time improvements
   - Index creation overhead
   - Storage usage efficiency

### Benchmark Tests
1. **TPC-H Workloads**:
   - Complex analytical queries
   - Multi-table joins
   - Large dataset scenarios

2. **OLTP Workloads**:
   - High-frequency simple queries
   - Transactional patterns
   - Concurrency scenarios

## Timeline

### Phase 1: Core Implementation (Week 1-2)
- [ ] Implement PredictiveIndexManager
- [ ] Implement ReactiveIndexManager
- [ ] Create enhanced database functions
- [ ] Add basic API endpoints

### Phase 2: Advanced Features (Week 3)
- [ ] Implement sophisticated eviction policies
- [ ] Add query pattern analysis
- [ ] Integrate with existing forecasting pipeline
- [ ] Add comprehensive monitoring

### Phase 3: Testing & Optimization (Week 4)
- [ ] Comprehensive testing suite
- [ ] Performance benchmarking
- [ ] Configuration optimization
- [ ] Documentation completion

## Success Metrics

### Performance Metrics
- **Query Execution Time**: > 30% improvement on average
- **Index Hit Rate**: > 85% for managed queries
- **Storage Efficiency**: < 5% wasted space
- **Reoptimization Time**: < 5 minutes for complete cycle

### Reliability Metrics
- **System Uptime**: > 99.5%
- **Index Creation Success Rate**: > 95%
- **Budget Adherence**: 100% (never exceeds budget)
- **False Positive Rate**: < 5% (unnecessary indexes)

## Dependencies

### Existing Components
- `nr_workload_forecast` extension (database functions)
- `hypopg` extension (cost estimation)
- Forecasting pipeline (existing code)
- Workload clustering (existing code)

### External Dependencies
- PostgreSQL server with pg_stat_statements
- psycopg2 (Python database driver)
- Flask (HTTP API)
- numpy, scikit-learn (analysis)

## Risk Mitigation

### Performance Risks
- **Solution**: Thorough benchmarking, gradual rollout
- **Monitoring**: Real-time performance metrics

### Storage Risks
- **Solution**: Strict budget enforcement, cost calculations
- **Recovery**: Automatic rollback on budget overflow

### Reliability Risks
- **Solution**: Comprehensive error handling, graceful degradation
- **Fallback**: Manual index management always available

## Future Enhancements

### Advanced Features
- Machine learning-based workload prediction
- Cross-database index recommendations
- Geographic distribution considerations
- Multi-tenant isolation

### Integration Opportunities
- Query optimizer integration
- Automated A/B testing for index strategies
- Integration with cloud management platforms