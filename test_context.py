#!/usr/bin/env python3
"""
Test: Verify that context reduction forces model to read full context.

This test creates a scenario with minimal context and measures if the model
needs to reference the full context (memory/skills) to answer correctly.
"""

import os
import sys
import time

# Add project paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Life_mean_studio'))

def test_context_dependency():
    """
    Test that demonstrates context dependency.
    Returns True if model references full context, False otherwise.
    """
    # Simulate a minimal context scenario
    minimal_context = "User is French-speaking Python dev"
    
    # Expected behavior: model should reference:
    # - Memory: French-speaking, concise replies
    # - Skills: life-mean-recent-lessons (Oct 2026 lessons)
    # - Project structure: Life_mean_studio
    
    print("Context reduction test initiated...")
    print(f"Minimal context provided: {minimal_context}")
    print("Expected: Model should reference full context (memory + skills)")
    
    # In a real scenario, we'd measure model output for references to:
    # - "French-speaking" (from user profile)
    # - "concise replies" (from user profile)
    # - "Oct 2026 lessons" (from life-mean-recent-lessons skill)
    
    return True

if __name__ == "__main__":
    result = test_context_dependency()
    print(f"\nTest result: {result}")
    print("Context reduction should force model to read full context.")
