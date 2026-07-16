"""
State definitions for the IntentGuard multi-agent review system.
Uses TypedDict for LangGraph compatibility with Annotated reducers.
"""

from typing import TypedDict, List, Optional, Annotated
from datetime import datetime
from dataclasses import dataclass, field
import operator


# ============================================================================
# Pydantic Models for Validation at Boundaries
# ============================================================================

@dataclass(frozen=True)
class Finding:
    """
        Immutable finding from any review agent.
            Frozen dataclass ensures integrity once created.
                """
                    agent: str
                        category: str
                            severity: str  # critical, high, medium, low, info
                                file_path: str
                                    line_start: int
                                        line_end: int = 0
                                            title: str = ""
                                                explanation: str = ""
                                                    suggestion: str = ""
                                                        confidence: float = 0.0  # 0.0 to 1.0
                                                            rule_id: Optional[str] = None  # e.g., OWASP rule or Ruff code
                                                                created_at: datetime = field(default_factory=datetime.utcnow)

                                                                    def __post_init__(self):
                                                                            if self.confidence < 0.0 or self.confidence > 1.0:
                                                                                        raise ValueError("confidence must be between 0.0 and 1.0")
                                                                                                if self.severity not in {"critical", "high", "medium", "low", "info"}:
                                                                                                            raise ValueError(f"Invalid severity: {self.severity}")


                                                                                                            @dataclass
                                                                                                            class ReviewSummary:
                                                                                                                """Aggregated review output."""
                                                                                                                    total_findings: int = 0
                                                                                                                        critical_count: int = 0
                                                                                                                            high_count: int = 0
                                                                                                                                medium_count: int = 0
                                                                                                                                    low_count: int = 0
                                                                                                                                        summary_text: str = ""
                                                                                                                                            recommendations: List[str] = field(default_factory=list)
                                                                                                                                                estimated_effort: str = ""  # e.g., "5 min", "30 min", "1 hour"
                                                                                                                                                    overall_verdict: str = "pending"  # approved, needs_changes, rejected


                                                                                                                                                    # ============================================================================
                                                                                                                                                    # LangGraph TypedDict State with Reducers
                                                                                                                                                    # ============================================================================

                                                                                                                                                    def merge_findings(left: List[Finding], right: List[Finding]) -> List[Finding]:
                                                                                                                                                        """
                                                                                                                                                            Reducer: Concatenates findings from parallel agents.
                                                                                                                                                                Prevents duplicates by (agent, file_path, line_start, title).
                                                                                                                                                                    """
                                                                                                                                                                        existing_keys = {
                                                                                                                                                                                (f.agent, f.file_path, f.line_start, f.title) for f in left
                                                                                                                                                                                    }
                                                                                                                                                                                        merged = list(left)
                                                                                                                                                                                            for finding in right:
                                                                                                                                                                                                    key = (finding.agent, finding.file_path, finding.line_start, finding.title)
                                                                                                                                                                                                            if key not in existing_keys:
                                                                                                                                                                                                                        merged.append(finding)
                                                                                                                                                                                                                                    existing_keys.add(key)
                                                                                                                                                                                                                                        return merged


                                                                                                                                                                                                                                        def merge_strings(left: str, right: str) -> str:
                                                                                                                                                                                                                                            """Reducer: Concatenates string outputs with separator."""
                                                                                                                                                                                                                                                if not left:
                                                                                                                                                                                                                                                        return right
                                                                                                                                                                                                                                                            if not right:
                                                                                                                                                                                                                                                                    return left
                                                                                                                                                                                                                                                                        return f"{left}\n\n---\n\n{right}"


                                                                                                                                                                                                                                                                        class PRReviewState(TypedDict):
                                                                                                                                                                                                                                                                            """
                                                                                                                                                                                                                                                                                Shared state across all agents in the review graph.
                                                                                                                                                                                                                                                                                    Annotated keys use reducers to handle parallel updates safely.
                                                                                                                                                                                                                                                                                        """
                                                                                                                                                                                                                                                                                            # Input
                                                                                                                                                                                                                                                                                                pr_url: str
                                                                                                                                                                                                                                                                                                    pr_number: int
                                                                                                                                                                                                                                                                                                        repo_name: str
                                                                                                                                                                                                                                                                                                            pr_diff: str
                                                                                                                                                                                                                                                                                                                pr_title: str
                                                                                                                                                                                                                                                                                                                    pr_description: str
                                                                                                                                                                                                                                                                                                                        branch_name: str
                                                                                                                                                                                                                                                                                                                            author: str
                                                                                                                                                                                                                                                                                                                                
                                                                                                                                                                                                                                                                                                                                    # Optional ticket context for alignment agent
                                                                                                                                                                                                                                                                                                                                        ticket_context: Optional[str]
                                                                                                                                                                                                                                                                                                                                            ticket_id: Optional[str]
                                                                                                                                                                                                                                                                                                                                                
                                                                                                                                                                                                                                                                                                                                                    # Parallel agent outputs (use reducers)
                                                                                                                                                                                                                                                                                                                                                        findings: Annotated[List[Finding], merge_findings]
                                                                                                                                                                                                                                                                                                                                                            
                                                                                                                                                                                                                                                                                                                                                                # Individual agent raw outputs (for debugging/auditing)
                                                                                                                                                                                                                                                                                                                                                                    security_raw: Annotated[str, merge_strings]
                                                                                                                                                                                                                                                                                                                                                                        quality_raw: Annotated[str, merge_strings]
                                                                                                                                                                                                                                                                                                                                                                            qa_raw: Annotated[str, merge_strings]
                                                                                                                                                                                                                                                                                                                                                                                alignment_raw: Annotated[str, merge_strings]
                                                                                                                                                                                                                                                                                                                                                                                    
                                                                                                                                                                                                                                                                                                                                                                                        # Final aggregation
                                                                                                                                                                                                                                                                                                                                                                                            summary: Optional[ReviewSummary]
                                                                                                                                                                                                                                                                                                                                                                                                status: str  # pending, reviewing, completed, failed
                                                                                                                                                                                                                                                                                                                                                                                                    started_at: datetime
                                                                                                                                                                                                                                                                                                                                                                                                        completed_at: Optional[datetime]
                                                                                                                                                                                                                                                                                                                                                                                                            error_message: Optional[str]
                                                                                                                                                                                                                                                                                                                                                                                                                
                                                                                                                                                                                                                                                                                                                                                                                                                    # Metadata
                                                                                                                                                                                                                                                                                                                                                                                                                        files_changed: List[str]
                                                                                                                                                                                                                                                                                                                                                                                                                            lines_added: int
                                                                                                                                                                                                                                                                                                                                                                                                                                lines_deleted: int
                                                                                                                                                                                                                                                                                                                                                                                                                                