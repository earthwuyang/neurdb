-- Automated Index Application and Monitoring Functions
-- Extensions to nr_workload_forecast for Phase 5 automation

-- Function to trigger automated analysis and index recommendations
CREATE OR REPLACE FUNCTION workload_forecast_auto_analyze(
    p_apply_safe_indexes BOOLEAN DEFAULT false,
    p_min_confidence FLOAT DEFAULT 0.7,
    p_max_indexes INTEGER DEFAULT 5
) RETURNS TABLE (
    analysis_id INTEGER,
    recommendations_generated INTEGER,
    indexes_applied INTEGER,
    success BOOLEAN,
    message TEXT
) AS $$
DECLARE
    v_start_time TIMESTAMP := NOW();
    v_analysis_id INTEGER := EXTRACT(EPOCH FROM v_start_time)::INTEGER;
    v_recommendations INTEGER := 0;
    v_applied INTEGER := 0;
    v_success BOOLEAN := false;
    v_message TEXT := 'Starting automated analysis';
    v_response TEXT;
    v_url TEXT := current_setting('workload_forecast.server_url') || '/index/recommendations/generate';
    v_request JSONB;
BEGIN
    -- Check if workload forecasting is enabled
    IF NOT current_setting('workload_forecast.enable')::BOOLEAN THEN
        v_message := 'Workload forecasting is disabled';
        RETURN NEXT;
        RETURN;
    END IF;

    -- Prepare request payload for AI engine
    v_request := jsonb_build_object(
        'database_host', 'localhost',
        'database_port', '5432',  -- Container port
        'database_name', 'neurdb',
        'database_user', 'neurdb',
        'optimization_approach', 'cost_based',
        'max_recommendations', p_max_indexes,
        'enable_hypothetical_analysis', true,
        'force_refresh', true
    );

    -- Send request to AI engine
    SELECT send_http_request(v_url, v_request::TEXT) INTO v_response;

    IF v_response IS NULL THEN
        v_message := 'Failed to communicate with AI engine';
        RETURN NEXT;
        RETURN;
    END IF;

    -- Parse response and store recommendations
    v_recommendations := process_ai_recommendations(v_response, v_analysis_id);

    -- Apply safe indexes if requested
    IF p_apply_safe_indexes AND v_recommendations > 0 THEN
        v_applied := apply_safe_recommendations(v_analysis_id, p_min_confidence, p_max_indexes);
    END IF;

    v_success := true;
    v_message := format('Analysis completed: %d recommendations, %d indexes applied', v_recommendations, v_applied);

    -- Update analysis log
    INSERT INTO neurdb_metrics.workload_summary (
        hour_bucket, total_queries, unique_templates,
        avg_execution_time_ms, created_at
    ) VALUES (
        date_trunc('hour', v_start_time), 0, 0, 0.0, v_start_time
    ) ON CONFLICT (hour_bucket) DO UPDATE SET
        created_at = GREATEST(workload_summary.created_at, EXCLUDED.created_at);

    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

-- Helper function to process AI recommendations
CREATE OR REPLACE FUNCTION process_ai_recommendations(
    p_response TEXT,
    p_analysis_id INTEGER
) RETURNS INTEGER AS $$
DECLARE
    v_response_json JSONB := p_response::JSONB;
    v_recommendations JSONB := v_response_json -> 'recommendations';
    v_count INTEGER := 0;
    v_rec JSONB;
    v_rec_id INTEGER;
BEGIN
    -- Insert each recommendation into the database
    FOR v_rec IN SELECT * FROM jsonb_array_elements(v_recommendations) LOOP
        INSERT INTO neurdb_metrics.index_recommendations (
            table_schema, table_name, columns, index_type, benefit_score,
            sql_statement, recommendation_source, confidence_score,
            estimated_size_mb, status
        ) VALUES (
            COALESCE((v_rec ->> 'schema')::TEXT, 'public'),
            (v_rec ->> 'table'),
            ARRAY[(v_rec ->> 'columns')],
            COALESCE((v_rec ->> 'index_type')::TEXT, 'btree'),
            COALESCE((v_rec ->> 'benefit_score')::FLOAT, 0.0),
            (v_rec ->> 'sql'),
            'workload_forecast',
            COALESCE((v_rec ->> 'confidence')::FLOAT, 0.5),
            (v_rec ->> 'estimated_size_mb')::FLOAT,
            'PENDING'
        ) RETURNING rec_id INTO v_rec_id;

        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- Function to apply safe recommendations
CREATE OR REPLACE FUNCTION apply_safe_recommendations(
    p_analysis_id INTEGER,
    p_min_confidence FLOAT DEFAULT 0.7,
    p_max_indexes INTEGER DEFAULT 5
) RETURNS INTEGER AS $$
DECLARE
    v_applied INTEGER := 0;
    v_rec RECORD;
    v_index_name TEXT;
    v_sql TEXT;
BEGIN
    -- Get pending recommendations that meet safety criteria
    FOR v_rec IN
        SELECT * FROM neurdb_metrics.index_recommendations
        WHERE status = 'PENDING'
        AND confidence_score >= p_min_confidence
        AND benefit_score > 0.1  -- Minimum benefit threshold
        AND risk_level IN ('LOW', 'MEDIUM')
        ORDER BY benefit_score DESC, confidence_score DESC
        LIMIT p_max_indexes
    LOOP
        -- Generate unique index name
        v_index_name := format('wf_auto_%s_%s_idx',
            v_rec.table_name,
            md5(array_to_string(v_rec.columns, '_'))
        );

        BEGIN
            -- Execute CREATE INDEX with safety timeout
            v_sql := format('CREATE INDEX CONCURRENTLY %s ON %I.%I (%s)',
                v_index_name,
                v_rec.table_schema,
                v_rec.table_name,
                array_to_string(v_rec.columns, ', ')
            );

            -- Add execution with timeout (simplified for now)
            EXECUTE v_sql;

            -- Mark as applied
            PERFORM neurdb_metrics.apply_index_recommendation(
                v_rec.rec_id,
                v_index_name
            );

            v_applied := v_applied + 1;

            -- Log the successful application
            RAISE NOTICE 'Applied index %: %', v_rec.rec_id, v_index_name;

        EXCEPTION WHEN OTHERS THEN
            -- Log error but continue with other recommendations
            UPDATE neurdb_metrics.index_recommendations
            SET status = 'FAILED',
                applied_at = NOW()
            WHERE rec_id = v_rec.rec_id;

            RAISE NOTICE 'Failed to apply index %: %', v_rec.rec_id, SQLERRM;
        END;
    END LOOP;

    RETURN v_applied;
END;
$$ LANGUAGE plpgsql;

-- Function to monitor applied indexes performance
CREATE OR REPLACE FUNCTION workload_forecast_monitor_indexes(
    p_days_back INTEGER DEFAULT 7
) RETURNS TABLE (
    index_name TEXT,
    table_name TEXT,
    days_since_creation INTEGER,
    usage_count BIGINT,
    avg_impact FLOAT,
    status TEXT,
    recommendation TEXT
) AS $$
DECLARE
    v_cutoff_date TIMESTAMP := NOW() - (p_days_back || ' days')::INTERVAL;
BEGIN
    RETURN QUERY
    SELECT
        ai.index_name,
        ai.table_name,
        EXTRACT(DAYS FROM NOW() - ai.applied_at)::INTEGER as days_since_creation,
        COALESCE(ai.usage_count, 0) as usage_count,
        COALESCE(ai.performance_impact, 0.0) as avg_impact,
        ai.status,
        CASE
            WHEN ai.usage_count = 0 THEN 'Consider dropping - unused'
            WHEN ai.performance_impact < 0.0 THEN 'Consider dropping - negative impact'
            WHEN ai.performance_impact < 0.1 THEN 'Review - low impact'
            ELSE 'Good performance'
        END as recommendation
    FROM neurdb_metrics.applied_indexes ai
    WHERE ai.applied_at >= v_cutoff_date
    ORDER BY ai.usage_count DESC, ai.performance_impact DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to get system health and statistics
CREATE OR REPLACE FUNCTION workload_forecast_system_health()
RETURNS TABLE (
    metric_name TEXT,
    value NUMERIC,
    status TEXT,
    description TEXT
) AS $$
BEGIN
    -- Extension status
    RETURN QUERY
    SELECT
        'extension_enabled'::TEXT,
        CASE WHEN current_setting('workload_forecast.enable')::BOOLEAN THEN 1.0 ELSE 0.0 END,
        CASE WHEN current_setting('workload_forecast.enable')::BOOLEAN THEN 'OK' ELSE 'DISABLED' END,
        'Workload forecasting extension status'::TEXT;

    -- Total recommendations
    RETURN QUERY
    SELECT
        'total_recommendations'::TEXT,
        COALESCE(COUNT(*), 0)::NUMERIC,
        CASE WHEN COUNT(*) > 0 THEN 'ACTIVE' ELSE 'IDLE' END,
        'Total number of index recommendations generated'::TEXT
    FROM neurdb_metrics.index_recommendations;

    -- Applied indexes
    RETURN QUERY
    SELECT
        'applied_indexes'::TEXT,
        COALESCE(COUNT(*), 0)::NUMERIC,
        CASE WHEN COUNT(*) > 0 THEN 'ACTIVE' ELSE 'NONE' END,
        'Number of indexes automatically applied'::TEXT
    FROM neurdb_metrics.applied_indexes WHERE status = 'ACTIVE';

    -- Recent workload
    RETURN QUERY
    SELECT
        'queries_last_24h'::TEXT,
        COALESCE(COUNT(*), 0)::NUMERIC,
        CASE WHEN COUNT(*) > 100 THEN 'HIGH' WHEN COUNT(*) > 10 THEN 'NORMAL' ELSE 'LOW' END,
        'Number of queries in last 24 hours'::TEXT
    FROM neurdb_metrics.workload_timeseries
    WHERE timestamp >= NOW() - INTERVAL '24 hours';

    -- Storage usage
    RETURN QUERY
    SELECT
        'index_storage_mb'::TEXT,
        COALESCE(SUM(size_mb), 0)::NUMERIC,
        CASE WHEN SUM(size_mb) > 1000 THEN 'HIGH' WHEN SUM(size_mb) > 100 THEN 'NORMAL' ELSE 'LOW' END,
        'Total storage used by recommended indexes'::TEXT
    FROM neurdb_metrics.applied_indexes;
END;
$$ LANGUAGE plpgsql;

-- Function to clean up unused indexes
CREATE OR REPLACE FUNCTION workload_forecast_cleanup_unused_indexes(
    p_days_unused INTEGER DEFAULT 30,
    p_auto_drop BOOLEAN DEFAULT false
) RETURNS TABLE (
    index_name TEXT,
    days_unused INTEGER,
    usage_count BIGINT,
    action_taken TEXT,
    impact_estimate FLOAT
) AS $$
DECLARE
    v_rec RECORD;
    v_impact_estimate FLOAT;
BEGIN
    FOR v_rec IN
        SELECT ai.*,
               EXTRACT(DAYS FROM NOW() - COALESCE(ai.last_used, ai.applied_at))::INTEGER as days_unused
        FROM neurdb_metrics.applied_indexes ai
        WHERE ai.status = 'ACTIVE'
        AND ai.recommended_by IN ('workload_forecast', 'cost_based')
        AND (
            ai.usage_count = 0 OR
            ai.last_used IS NULL OR
            ai.last_used < NOW() - (p_days_unused || ' days')::INTERVAL
        )
        AND ai.index_name NOT LIKE '%_pkey'  -- Don't drop primary keys
        AND ai.index_name NOT LIKE '%_unique'  -- Don't drop unique constraints
    LOOP
        -- Estimate impact (simplified)
        v_impact_estimate := COALESCE(v_rec.performance_impact, 0.0);

        IF p_auto_drop THEN
            BEGIN
                -- Drop the index
                EXECUTE format('DROP INDEX IF EXISTS %I', v_rec.index_name);

                -- Mark as dropped
                UPDATE neurdb_metrics.applied_indexes
                SET status = 'DROPPED',
                    last_used = NOW()
                WHERE index_name = v_rec.index_name;

                RETURN NEXT;
            EXCEPTION WHEN OTHERS THEN
                RETURN NEXT;
            END;
        ELSE
            RETURN NEXT;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Function to get recent activity log
CREATE OR REPLACE FUNCTION workload_forecast_activity_log(
    p_hours_back INTEGER DEFAULT 24
) RETURNS TABLE (
    event_timestamp TIMESTAMP,
    activity_type TEXT,
    details TEXT,
    status TEXT
) AS $$
BEGIN
    -- Recent index recommendations
    RETURN QUERY
    SELECT
        created_at as event_timestamp,
        'INDEX_RECOMMENDATION' as activity_type,
        format('Table: %I.%I, Columns: %s, Benefit: %.2f', table_schema, table_name, array_to_string(columns, ', '), benefit_score) as details,
        status as status
    FROM neurdb_metrics.index_recommendations
    WHERE created_at >= NOW() - (p_hours_back || ' hours')::INTERVAL;

    -- Recent index applications
    RETURN QUERY
    SELECT
        applied_at as event_timestamp,
        'INDEX_APPLICATION' as activity_type,
        format('Index: %s on %I.%I', index_name, table_schema, table_name) as details,
        status as status
    FROM neurdb_metrics.applied_indexes
    WHERE applied_at >= NOW() - (p_hours_back || ' hours')::INTERVAL;

    -- System analysis events (if we had an analysis log table)
    RETURN QUERY
    SELECT
        hour_bucket as event_timestamp,
        'WORKLOAD_ANALYSIS' as activity_type,
        format('Queries: %L, Templates: %L', total_queries, unique_templates) as details,
        'COMPLETED' as status
    FROM neurdb_metrics.workload_summary
    WHERE hour_bucket >= NOW() - (p_hours_back || ' hours')::INTERVAL;
END;
$$ LANGUAGE plpgsql;

-- Create a simple view for recent recommendations
CREATE OR REPLACE VIEW neurdb_metrics.recent_recommendations AS
SELECT
    rec_id,
    created_at,
    table_schema,
    table_name,
    columns,
    index_type,
    benefit_score,
    confidence_score,
    status,
    sql_statement
FROM neurdb_metrics.index_recommendations
WHERE status = 'PENDING'
ORDER BY benefit_score DESC, confidence_score DESC
LIMIT 10;

-- Create a view for system status
CREATE OR REPLACE VIEW neurdb_metrics.system_status AS
SELECT
    current_setting('workload_forecast.enable')::BOOLEAN as forecasting_enabled,
    (SELECT COUNT(*) FROM neurdb_metrics.index_recommendations WHERE status = 'PENDING') as pending_recommendations,
    (SELECT COUNT(*) FROM neurdb_metrics.applied_indexes WHERE status = 'ACTIVE') as applied_indexes,
    (SELECT COUNT(*) FROM neurdb_metrics.workload_timeseries WHERE timestamp >= NOW() - INTERVAL '24 hours') as queries_today;

-- Grant permissions
GRANT EXECUTE ON FUNCTION workload_forecast_auto_analyze TO neurdb;
GRANT EXECUTE ON FUNCTION workload_forecast_monitor_indexes TO neurdb;
GRANT EXECUTE ON FUNCTION workload_forecast_system_health TO neurdb;
GRANT EXECUTE ON FUNCTION workload_forecast_cleanup_unused_indexes TO neurdb;
GRANT EXECUTE ON FUNCTION workload_forecast_activity_log TO neurdb;
GRANT SELECT ON neurdb_metrics.recent_recommendations TO neurdb;
GRANT SELECT ON neurdb_metrics.system_status TO neurdb;