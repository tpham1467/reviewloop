"""Step lists for each flow (spec 5.1 and 5.2)."""

from reviewloop.pipeline.engine import Step
from reviewloop.pipeline.steps.follow_up.build_follow_up_context import BuildFollowUpContext
from reviewloop.pipeline.steps.follow_up.follow_up_review import FollowUpReview
from reviewloop.pipeline.steps.follow_up.follow_up_summary import FollowUpSummary
from reviewloop.pipeline.steps.follow_up.force_push_and_lineage import ForcePushAndLineage
from reviewloop.pipeline.steps.follow_up.in_progress_comment import InProgressComment
from reviewloop.pipeline.steps.follow_up.load_thread_inputs import LoadThreadInputs
from reviewloop.pipeline.steps.follow_up.post_follow_up_comments import PostFollowUpComments
from reviewloop.pipeline.steps.follow_up.resolve_target_turn import ResolveTargetTurn
from reviewloop.pipeline.steps.follow_up.security_rescan import SecurityRescan
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


def _shared() -> list[Step]:
    """The steps both flows start with. load-config and get-pr-info come first: get-diff needs `paths.exclude`, a
    skipped draft must not get a "Reviewing..." comment, and load-config checks the agent's auth before any comment."""
    return [LoadConfig(), GetPrInfo(), InitContext()]


def _review() -> list[Step]:
    """The initial review after the shared steps; a recheck with no state marker runs it too (ruling R35)."""
    return [
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


def initial_flow() -> list[Step]:
    """Spec 5.1."""
    return [*_shared(), *_review()]


def follow_up_flow() -> list[Step]:
    """Spec 5.2 (`/reviewloop recheck`). get-diff and clean are the initial flow's steps; get-diff diffs from the
    base force-push-and-lineage picked.

    A secret thread stays `not_fixed` when gitleaks is missing or fails: it fails closed (ruling R77).
    """
    return [
        *_shared(),
        ResolveTargetTurn(_review()),
        InProgressComment(),
        ForcePushAndLineage(),
        GetDiff(),
        LoadThreadInputs(),
        BuildFollowUpContext(),
        SecurityRescan(),
        FollowUpReview(),
        PostFollowUpComments(),
        FollowUpSummary(),
        Clean(),
    ]
