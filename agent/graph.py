"""
IntentGuard Graph Orchestrator
Implements parallel multi-agent execution using LangGraph's fan-out/fan-in pattern.
All 4 agents run concurrently in the same superstep; synthesizer waits for all.
"""

from typing import Dict, Any, Literal
from datetime import datetime

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from agent.state import PRReviewState
from agent.nodes import (
    security_agent,
    quality_agent,
    qa_agent,
    alignment_agent,
    synthesizer_node,
)


# ============================================================================
# Graph Builder
# ============================================================================

def create_review_graph(checkpointer=None):
    """
    Build and compile the IntentGuard review graph.
    
    Parallel Execution Architecture:
    =================================
    
        START ──┬──→ security_agent ──┐
                ├──→ quality_agent  ──┤
                ├──→ qa_agent ────────┼──→ synthesizer_node ──→ END
                └──→ alignment_agent ─┘
    
    Key Design Decisions:
    - All 4 agents execute in PARALLEL (same superstep in LangGraph)
    - Each agent writes to the shared 'findings' list via Annotated reducer
    - Synthesizer ONLY runs after ALL 4 agents complete (fan-in gate)
    - No Send API needed: standard edges achieve true parallelism here
    """
    
    # Initialize graph with our state schema
    builder = StateGraph(state_schema=PRReviewState)
    
    # -------------------------------------------------------------------------
    # Register all nodes
    # -------------------------------------------------------------------------
    builder.add_node("security_agent", security_agent)
    builder.add_node("quality_agent", quality_agent)
    builder.add_node("qa_agent", qa_agent)
    builder.add_node("alignment_agent", alignment_agent)
    builder.add_node("synthesizer", synthesizer_node)
    
    # -------------------------------------------------------------------------
    # FAN-OUT: START → all 4 agents in parallel
    # -------------------------------------------------------------------------
    # LangGraph executes all outgoing edges from START concurrently
    builder.add_edge(START, "security_agent")
    builder.add_edge(START, "quality_agent")
    builder.add_edge(START, "qa_agent")
    builder.add_edge(START, "alignment_agent")
    
    # -------------------------------------------------------------------------
    # FAN-IN: All 4 agents → synthesizer
    # -------------------------------------------------------------------------
    # Synthesizer waits for ALL 4 predecessors to complete before running
    # This is the critical synchronization point
    builder.add_edge("security_agent", "synthesizer")
    builder.add_edge("quality_agent", "synthesizer")
    builder.add_edge("qa_agent", "synthesizer")
    builder.add_edge("alignment_agent", "synthesizer")
    
    # -------------------------------------------------------------------------
    # Completion
    # -------------------------------------------------------------------------
    builder.add_edge("synthesizer", END)
    
    # Compile with checkpointing for state persistence and resumability
    if checkpointer is None:
        checkpointer = InMemorySaver()
    
    return builder.compile(checkpointer=checkpointer)


# ============================================================================
# One-shot execution helper
# ============================================================================

def run_review(
    pr_url: str,
    pr_number: int,
    repo_name: str,
    pr_diff: str,
    pr_title: str = "",
    pr_description: str = "",
    branch_name: str = "",
    author: str = "",
    ticket_context: str = None,
    ticket_id: str = None,
    files_changed: list = None,
    lines_added: int = 0,
    lines_deleted: int = 0,
) -> Dict[str, Any]:
    """
    Execute a complete review in one call.
    
    Creates the graph, initializes state, runs all 4 agents in parallel,
    synthesizes results, and returns the final state.
    """
    graph = create_review_graph()
    
    # Build initial state
    initial_state: PRReviewState = {
        "pr_url": pr_url,
        "pr_number": pr_number,
        "repo_name": repo_name,
        "pr_diff": pr_diff,
        "pr_title": pr_title,
        "pr_description": pr_description,
        "branch_name": branch_name,
        "author": author,
        "ticket_context": ticket_context,
        "ticket_id": ticket_id,
        "findings": [],
        "security_raw": "",
        "quality_raw": "",
        "qa_raw": "",
        "alignment_raw": "",
        "summary": None,
        "status": "reviewing",
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "error_message": None,
        "files_changed": files_changed or [],
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
    }
    
    # Execute with thread_id for traceability
    config = {
        "configurable": {
            "thread_id": f"intentguard-{repo_name.replace('/', '-')}-{pr_number}"
        }
    }
    
    final_state = graph.invoke(initial_state, config)
    return final_state


# ============================================================================
# Streaming execution (for real-time dashboards)
# ============================================================================

def stream_review(
    pr_url: str,
    pr_number: int,
    repo_name: str,
    pr_diff: str,
    **kwargs
):
    """
    Stream review events as they happen.
    Yields state snapshots after each superstep.
    Useful for live dashboards or progress indicators.
    """
    graph = create_review_graph()
    
    initial_state: PRReviewState = {
        "pr_url": pr_url,
        "pr_number": pr_number,
        "repo_name": repo_name,
        "pr_diff": pr_diff,
        "pr_title": kwargs.get("pr_title", ""),
        "pr_description": kwargs.get("pr_description", ""),
        "branch_name": kwargs.get("branch_name", ""),
        "author": kwargs.get("author", ""),
        "ticket_context": kwargs.get("ticket_context"),
        "ticket_id": kwargs.get("ticket_id"),
        "findings": [],
        "security_raw": "",
        "quality_raw": "",
        "qa_raw": "",
        "alignment_raw": "",
        "summary": None,
        "status": "reviewing",
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "error_message": None,
        "files_changed": kwargs.get("files_changed", []),
        "lines_added": kwargs.get("lines_added", 0),
        "lines_deleted": kwargs.get("lines_deleted", 0),
    }
    
    config = {
        "configurable": {
            "thread_id": f"intentguard-stream-{repo_name.replace('/', '-')}-{pr_number}"
        }
    }
    
    for event in graph.stream(initial_state, config, stream_mode="updates"):
        yield event
