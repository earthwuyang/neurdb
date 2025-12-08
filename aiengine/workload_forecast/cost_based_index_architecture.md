# Cost-Based Index Advisor Architecture
## PostgreSQL Optimizer + DTA-Inspired Approach

### Core Principles

1. **Real Cost Model Integration**: Use PostgreSQL's actual cost estimation functions
2. **Combinatorial Search Space**: Enumerate index combinations systematically
3. **Progressive Optimization**: DTA-style anytime algorithm with increasing quality
4. **Heuristic Pruning**: Smart search space reduction using cost bounds
5. **Workload-Aware Optimization**: Consider query frequency and importance weights

### Architecture Components

#### 1. Cost Model Integration Layer
```python
class PostgreSQLCostModel:
    """Integration with PostgreSQL's real cost estimation"""

    def estimate_query_cost(self, query_plan, existing_indexes, candidate_indexes):
        """Estimate actual execution cost using PostgreSQL cost model"""
        # Use hypopg to create hypothetical indexes
        # Get EXPLAIN (ANALYZE, BUFFERS) with and without candidate indexes
        # Extract actual cost metrics from plan

    def estimate_index_maintenance_cost(self, index_candidate):
        """Calculate storage and maintenance cost using PostgreSQL formulas"""
        # pg_total_relation_size() for storage cost
        # Maintenance cost based on index type and update frequency

    def calculate_workload_cost(self, workload_queries, index_config):
        """Calculate total workload cost with given index configuration"""
        weighted_cost = 0
        for query, frequency in workload_queries:
            query_cost = self.estimate_query_cost(query, None, index_config)
            weighted_cost += query_cost * frequency
        return weighted_cost
```

#### 2. Search Space Enumerator
```python
class IndexSearchSpace:
    """Systematic enumeration of candidate index configurations"""

    def __init__(self, max_indexes_per_table=3, max_total_indexes=10):
        self.max_indexes_per_table = max_indexes_per_table
        self.max_total_indexes = max_total_indexes

    def generate_single_column_candidates(self, schema_info, query_workload):
        """Generate all viable single-column index candidates"""
        # Identify columns appearing in WHERE, JOIN, ORDER BY clauses
        # Filter by selectivity and update frequency

    def generate_multi_column_candidates(self, schema_info, query_workload):
        """Generate compound index candidates"""
        # Use column co-occurrence analysis from query workload
        # Consider column order optimization

    def prune_by_selectivity(self, candidates, statistics):
        """Remove candidates with poor selectivity"""
        # Use PostgreSQL statistics or sample data

    def prune_by_size(self, candidates, max_size_mb):
        """Remove candidates that exceed size constraints"""
```

#### 3. DTA-Inspired Progressive Optimizer
```python
class AnytimeIndexOptimizer:
    """DTA-style anytime algorithm for progressive index optimization"""

    def __init__(self, time_limit_seconds=60, quality_targets=None):
        self.time_limit = time_limit
        self.quality_targets = quality_targets

    def optimize_progressively(self, workload, schema):
        """Main DTA-style progressive optimization loop"""
        start_time = time.time()
        best_solution = IndexConfiguration()
        quality_improvements = []

        # Phase 1: Quick single-index greedy algorithm
        greedy_solution = self.greedy_single_index(workload, schema)
        best_solution = max(best_solution, greedy_solution, key=lambda x: x.benefit_score)

        # Phase 2: Iterative improvement with limited search
        while time.time() - start_time < self.time_limit * 0.5:
            improved_solution = self.local_search(best_solution, workload, schema)
            if improved_solution.benefit_score > best_solution.benefit_score:
                best_solution = improved_solution
                quality_improvements.append((time.time() - start_time,
                                               best_solution.benefit_score))

        # Phase 3: Exhaustive search for small problem instances
        if len(workload.queries) <= 10:
            exhaustive_solution = self.exhaustive_search(workload, schema)
            best_solution = max(best_solution, exhaustive_solution,
                              key=lambda x: x.benefit_score)

        return ProgressiveResult(best_solution, quality_improvements)

    def greedy_single_index(self, workload, schema):
        """Greedy algorithm adding best single index repeatedly"""
        current_config = IndexConfiguration()
        remaining_budget = self.max_total_indexes

        while remaining_budget > 0:
            best_candidate = None
            best_improvement = 0

            # Try adding each remaining candidate index
            for candidate in self.generate_candidates(schema, workload):
                if candidate in current_config.indexes:
                    continue

                test_config = current_config + [candidate]
                improvement = self.calculate_improvement(workload, current_config, test_config)

                if improvement > best_improvement:
                    best_improvement = improvement
                    best_candidate = candidate

            if best_candidate and best_improvement > 0:
                current_config.add_index(best_candidate)
                remaining_budget -= 1
            else:
                break

        return current_config
```

#### 4. Search Space Pruning Strategies
```python
class SearchPruningStrategies:
    """Advanced pruning techniques for combinatorial search"""

    def upper_bound_pruning(self, candidate_set, current_best_cost):
        """Prune if theoretical minimum cost already exceeds current best"""
        remaining_potential = self.calculate_remaining_potential(candidate_set)
        return current_best_cost <= remaining_potential

    def similarity_pruning(self, candidate_configurations, similarity_threshold=0.9):
        """Remove similar configurations to avoid redundant evaluation"""
        # Use Jaccard similarity between index sets

    def cost_based_pruning(self, partial_solution, workload):
        """Early pruning based on partial cost estimation"""
        # Estimate minimum possible improvement from remaining indexes
        # Prune if theoretical optimum can't beat current best

    def workload_correlation_pruning(self, index_candidates, workload):
        """Remove indexes with low workload correlation"""
        # Use frequency and cost correlation analysis
```

#### 5. Index Configuration Representation
```python
@dataclass
class IndexConfiguration:
    """Represents a set of indexes for cost evaluation"""
    indexes: List[IndexCandidate]
    total_cost: float = 0.0
    storage_cost: float = 0.0
    maintenance_cost: float = 0.0
    benefit_score: float = 0.0

    def calculate_comprehensive_cost(self, workload, cost_model):
        """Calculate total cost including storage, maintenance, and query execution"""
        query_cost = cost_model.calculate_workload_cost(workload.queries, self)
        storage_cost = cost_model.estimate_index_maintenance_cost(self.indexes)
        maintenance_cost = self.estimate_maintenance_overhead(workload.update_frequency)

        self.total_cost = query_cost + storage_cost + maintenance_cost
        self.benefit_score = self.calculate_benefit_score(workload)

    def calculate_benefit_score(self, workload):
        """Multi-factor benefit calculation"""
        # Cost improvement weighted by query frequency
        # Storage efficiency score
        # Maintenance overhead penalty
        # Query diversity coverage
```

### Integration with PostgreSQL Query Planner

#### 1. Real Plan Analysis
```python
class QueryPlanAnalyzer:
    """Analyze actual PostgreSQL execution plans"""

    def analyze_with_indexes(self, query, index_configuration):
        """Get execution plan with hypothetical indexes"""
        # Use hypopg to create hypothetical indexes
        # Execute EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        # Extract cost, execution time, I/O statistics

    def calculate_plan_improvement(self, original_plan, optimized_plan):
        """Calculate improvement metrics between plans"""
        cost_improvement = (original_plan.total_cost - optimized_plan.total_cost) / original_plan.total_cost
        time_improvement = (original_plan.execution_time - optimized_plan.execution_time) / original_plan.execution_time
        io_improvement = (original_plan.shared_blocks - optimized_plan.shared_blocks) / original_plan.shared_blocks

        return PlanImprovement(cost_improvement, time_improvement, io_improvement)
```

#### 2. Workload-Aware Optimization
```python
class WorkloadOptimizer:
    """Optimize for entire workload rather than individual queries"""

    def optimize_workload(self, workload, schema):
        """Main optimization entry point"""
        # Phase 1: Index candidate generation
        candidates = self.generate_index_candidates(workload, schema)

        # Phase 2: Progressive optimization
        optimizer = AnytimeIndexOptimizer(time_limit=60)
        result = optimizer.optimize_progressively(workload, candidates)

        # Phase 3: Validation and refinement
        final_solution = self.validate_and_refine(result.best_solution, workload)

        return IndexRecommendation(
            indexes=final_solution.indexes,
            estimated_cost_reduction=final_solution.benefit_score,
            confidence=self.calculate_confidence(result.quality_improvements),
            optimization_time=result.optimization_time
        )
```

### Key Differences from Current Implementation

1. **Cost-Based vs Pattern-Based**: Uses PostgreSQL's real cost model instead of heuristics
2. **Combinatorial Search**: Explores index combinations rather than individual indexes
3. **Progressive Quality**: DTA-style anytime algorithm with increasing quality guarantees
4. **Real Validation**: Uses actual PostgreSQL query planner for evaluation
5. **Workload-Aware**: Optimizes for total workload cost, not individual queries
6. **Advanced Pruning**: Sophisticated search space reduction strategies

### Implementation Strategy

#### Phase 1: Core Cost Model Integration
- Integrate with PostgreSQL's cost estimation functions
- Implement real query cost measurement using hypopg
- Create workload cost aggregation framework

#### Phase 2: Search Space Management
- Implement candidate generation algorithms
- Add pruning strategies for combinatorial explosion
- Create index configuration representation

#### Phase 3: Progressive Optimization
- Implement DTA-style anytime algorithm
- Add quality-aware search strategies
- Create progressive result reporting

#### Phase 4: Validation and Integration
- End-to-end testing with real workloads
- Performance benchmarking against current implementation
- Integration with existing Phase 4 API

This architecture will provide true cost-based optimization similar to PostgreSQL's internal optimizer while adding DTA-like progressive search capabilities for practical performance.