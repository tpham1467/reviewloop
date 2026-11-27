"""Step lists for each flow (spec 5.1 and 5.2)."""

from reviewloop.pipeline.engine import Step
from reviewloop.pipeline.steps.initial.build_context import BuildContext
from reviewloop.pipeline.steps.initial.clean import Clean
from reviewloop.pipeline.steps.initial.detect_stack import DetectStack
from reviewloop.pipeline.steps.initial.fetch_previous_comments import FetchPreviousComments
from reviewloop.pipeline.steps.initial.get_diff import GetDiff
from reviewloop.pipeline.steps.initial.get_pr_info import GetPrInfo
from reviewloop.pipeline.steps.initial.init_context import InitContext
from reviewloop.pipeline.steps.initial.initial_review import InitialReview
from reviewloop.pipeline.steps.initial.large_diff_review import LargeDiffReview
from reviewloop.pipeline.steps.initial.load_config import LoadConfig
from reviewloop.pipeline.steps.initial.post_comment import PostComment
from reviewloop.pipeline.steps.initial.pr_overview import PrOverview
from reviewloop.pipeline.steps.initial.pre_review_comment import PreReviewComment
from reviewloop.pipeline.steps.initial.quality_gate import QualityGate
from reviewloop.pipeline.steps.initial.resolve_review_context import ResolveReviewContext
from reviewloop.pipeline.steps.initial.security_branch import SecurityBranch
from reviewloop.pipeline.steps.initial.security_merge import SecurityMerge
from reviewloop.pipeline.steps.initial.summarize_context import SummarizeContext
from reviewloop.pipeline.steps.initial.summary import Summary


def initial_flow() -> list[Step]:
    """Spec 5.1. load-config and get-pr-info come first: get-diff needs `paths.exclude`, and a skipped draft must
    not get a "Reviewing..." comment."""
    return [
        LoadConfig(),
        GetPrInfo(),
        InitContext(),
        PreReviewComment(),
        FetchPreviousComments(),
        GetDiff(),
        ResolveReviewContext(),
        DetectStack(),
        SummarizeContext(),
        BuildContext(),
        LargeDiffReview(),
        InitialReview(),
        SecurityBranch(),
        PrOverview(),
        SecurityMerge(),
        QualityGate(),
        PostComment(),
        Summary(),
        Clean(),
    ]


def follow_up_flow() -> list[Step]:
    """Spec 5.2 (`/reviewloop recheck`); Task 12 fills it. Empty until then."""
    return []
