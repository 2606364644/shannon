from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.models import CodeIndex, ParameterSource


def test_source_point_basic_fields():
    sp = SourcePoint(
        id="app/routes/allocations.js:displayAllocations:11::userId::18",
        entry_point_id="app/routes/allocations.js:displayAllocations:11",
        param_name="userId",
        source_type=ParameterSource.PATH_PARAM,
        expression="req.params.userId",
        file_path="app/routes/allocations.js",
        line=18,
        validation="parseInt()",
        confidence=0.9,
        rule_id="ts-express-path",
    )
    assert sp.param_name == "userId"
    assert sp.source_type == ParameterSource.PATH_PARAM
    assert sp.validation == "parseInt()"
    assert sp.needs_review is False  # default


def test_code_index_has_source_points_field():
    ci = CodeIndex(
        repository="r", language="typescript", total_blocks=0,
        total_entry_points=0, total_chains=0, blocks=[], edges=[],
        entry_points=[], chains=[],
    )
    assert ci.source_points == []  # default empty list
