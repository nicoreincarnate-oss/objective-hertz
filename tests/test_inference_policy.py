from shared.inference_policy import choose_inference_lane


def test_kirito_live_voice_prefers_cloud_quality():
    decision = choose_inference_lane("kirito", "live_voice")

    assert decision.primary_model == "smart"
    assert decision.fallback_model == "fast"


def test_memory_heavy_work_prefers_local_heavy():
    assert choose_inference_lane("kirito", "memory_query").primary_model == "local-heavy"
    assert choose_inference_lane("titan", "memory_consolidation").primary_model == "local-heavy"
    assert choose_inference_lane("openjarvis", "background_research").primary_model == "local-heavy"


def test_pipeline_customer_facing_work_stays_high_quality():
    assert choose_inference_lane("pipeline", "lead_research").primary_model == "smart"
    assert choose_inference_lane("pipeline", "email_compose").primary_model == "smart"


def test_openjarvis_orchestration_stays_genius():
    decision = choose_inference_lane("openjarvis", "orchestration")

    assert decision.primary_model == "genius"
    assert decision.fallback_model == "smart"
