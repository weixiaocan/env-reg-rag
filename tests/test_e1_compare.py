from scripts.e1_compare import _guardrail_transitions, _net_effect_gate


def test_fixed_tracking_rescues_are_not_counted_twice():
    worse = [{"evidence": f"G{i:03d}[0]"} for i in range(1, 15)]
    saved = [f"G{i:03d}[0]" for i in range(1, 8)]
    rescued = [{"evidence": evidence} for evidence in saved]
    gate = _net_effect_gate(worse, saved, rescued)
    assert gate == {
        "worse": 14,
        "saved": 7,
        "fixed_tracking_rescued": 7,
        "overlap": 7,
        "ok": False,
    }


def test_guardrail_categories_include_miss_to_miss_and_close():
    def case(ranks):
        return {"evidence_results": [
            {"evidence_index": index, "match_rank": rank}
            for index, rank in enumerate(ranks)
        ]}

    transitions = _guardrail_transitions(
        {"G001": case([None, None, 1, 8])},
        {"G001": case([None, 4, 8, 3])},
    )
    assert transitions["unchanged"] == ["G001[0]"]
    assert transitions["rescued"] == ["G001[1]"]
    assert len(transitions["worse"]) == 1
    assert len(transitions["better"]) == 1
    assert sum(len(value) for value in transitions.values()) == 4
