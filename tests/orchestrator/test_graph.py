"""Tests for PhynaiDependencyGraph."""

from phynai.orchestrator.graph import PhynaiDependencyGraph


class TestDependencyGraph:
    def _make(self) -> PhynaiDependencyGraph:
        return PhynaiDependencyGraph()

    def test_add_edge_and_eligible_no_deps(self):
        g = self._make()
        g.add_edge("a", "b")
        # b has no deps so it is eligible
        assert g.is_eligible("b") is True

    def test_is_eligible_false_when_dep_not_complete(self):
        g = self._make()
        g.add_edge("a", "b")  # a depends on b
        assert g.is_eligible("a") is False

    def test_mark_complete_makes_dependent_eligible(self):
        g = self._make()
        g.add_edge("a", "b")
        assert g.is_eligible("a") is False
        g.mark_complete("b")
        assert g.is_eligible("a") is True

    def test_get_blocked_by_returns_incomplete_deps(self):
        g = self._make()
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        blocked = g.get_blocked_by("a")
        assert set(blocked) == {"b", "c"}
        g.mark_complete("b")
        assert g.get_blocked_by("a") == ["c"]

    def test_has_cycle_detects_cycle(self):
        g = self._make()
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        assert g.has_cycle() is True

    def test_has_cycle_false_for_dag(self):
        g = self._make()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        assert g.has_cycle() is False

    def test_remove_cleans_up_node(self):
        g = self._make()
        g.add_edge("a", "b")
        g.add_edge("c", "b")
        g.remove("b")
        assert "b" not in g
        # a and c should no longer be blocked by b
        assert g.is_eligible("a") is True
        assert g.is_eligible("c") is True

    def test_unknown_node_is_eligible(self):
        g = self._make()
        assert g.is_eligible("unknown") is True
