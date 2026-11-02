"""Weighted admission for normal and setscene tasks, without modifying BitBake."""
# SPDX-License-Identifier: MIT
import re

import bb
from bb.runqueue import RunQueueSchedulerSpeed, taskname_from_tid


class WeightedScheduler(RunQueueSchedulerSpeed):
    name = "c2a-weighted"

    def __init__(self, runqueue, rqdata):
        super().__init__(runqueue, rqdata)
        self.budget = self._positive_integer(
            "C2A_RUNQUEUE_BUDGET",
            runqueue.cfgData.getVar("C2A_RUNQUEUE_BUDGET") or str(runqueue.number_tasks),
        )
        self.default_weight = self._positive_integer(
            "C2A_RUNQUEUE_DEFAULT_WEIGHT",
            runqueue.cfgData.getVar("C2A_RUNQUEUE_DEFAULT_WEIGHT") or "4",
        )
        self.setscene_weight = self._positive_integer(
            "C2A_RUNQUEUE_SETSCENE_WEIGHT",
            runqueue.cfgData.getVar("C2A_RUNQUEUE_SETSCENE_WEIGHT") or "1",
        )
        self.weights = {}
        for item in (runqueue.cfgData.getVar("C2A_RUNQUEUE_WEIGHTS") or "").split():
            task, separator, value = item.partition("=")
            if not separator or not re.fullmatch(r"do_[A-Za-z0-9_]+", task):
                bb.fatal("C2A_RUNQUEUE_WEIGHTS: expected do_task=positive_integer, got %r" % item)
            self.weights[task] = self._positive_integer("C2A_RUNQUEUE_WEIGHTS: " + task, value)

        # Setscene bypasses scheduler.next(), but both paths call this method.
        # Wrap only this executor instance; do not mutate the BitBake class.
        self._original_can_start_task = runqueue.can_start_task
        runqueue.can_start_task = self.can_start_task
        bb.note("C2A weighted scheduler: budget %d, normal weight %d, setscene weight %d, overrides %s" % (
            self.budget, self.default_weight, self.setscene_weight, " ".join(
                "%s=%d" % item for item in sorted(self.weights.items())) or "none"))

    @staticmethod
    def _positive_integer(name, value):
        try:
            result = int(value)
        except (ValueError, TypeError):
            bb.fatal("%s must be a positive integer, got %r" % (name, value))
        if result < 1:
            bb.fatal("%s must be a positive integer, got %r" % (name, value))
        return result

    def used_budget(self):
        # Failed tasks stay in runq_running, including with bitbake -k.
        normal = self.rq.runq_running.difference(self.rq.runq_complete, self.rq.failed_tids)
        return sum(self.weights.get(taskname_from_tid(tid), self.default_weight)
                   for tid in normal) + sum(
                       self.weights.get(taskname_from_tid(tid) + "_setscene", self.setscene_weight)
                       for tid in self.rq.sq_live)

    def can_start_task(self):
        # Admission has no candidate argument: the last start can exceed budget
        # by at most max(weight)-1. An empty queue always admits one task, even
        # when its weight exceeds the entire budget, so progress is preserved.
        return self._original_can_start_task() and self.used_budget() < self.budget
