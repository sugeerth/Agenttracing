"""Generate grafana/dashboards/*.json: six dashboards, one question each,
every panel a question, every interval drawn (never a bare point)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
BOOT = "The interval is a stratified bootstrap over the runs recorded (resampled within each task), not a population claim."
WILSON = "The interval is a 95% Wilson interval on the runs recorded, not a population claim."
SYN = "Every series carries synthetic=\"true|false\" from the trace's harness note; a demo never reads as production."

class D:
    def __init__(self, uid, title, description, tags):
        self.uid, self.title, self.description, self.tags = uid, title, description, tags
        self.panels, self.next_id, self.types = [], 1, set()

    def add(self, panel, x, y, w, h):
        panel = dict(panel)
        panel["id"] = self.next_id
        self.next_id += 1
        panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        panel["datasource"] = DS
        self.types.add(panel["type"])
        self.panels.append(panel)

    def build(self):
        requires = [{"type": "grafana", "id": "grafana", "name": "Grafana", "version": "11.0.0"},
                    {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"}]
        for t in sorted(self.types):
            requires.append({"type": "panel", "id": t, "name": t, "version": ""})
        return {
            "__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "description": "",
                          "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus"}],
            "__requires": requires,
            "uid": self.uid, "title": self.title, "description": self.description,
            "tags": self.tags, "timezone": "browser", "editable": True, "graphTooltip": 1,
            "schemaVersion": 42, "version": 1, "refresh": "1m",
            "time": {"from": "now-1h", "to": "now"},
            "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m"]},
            "templating": {"list": [{
                "type": "datasource", "name": "DS_PROMETHEUS", "label": "Prometheus", "query": "prometheus",
                "hide": 0, "refresh": 1, "regex": "", "multi": False, "includeAll": False,
                "current": {"selected": True, "text": "Prometheus", "value": "prometheus"},
                "options": [], "description": "the Prometheus that scrapes metrics.prom",
            }]},
            "annotations": {"list": []}, "links": [], "panels": self.panels,
        }

def target(expr, ref, legend="", instant=False, table=False):
    t = {"datasource": DS, "refId": ref, "expr": expr, "legendFormat": legend, "editorMode": "code"}
    if instant or table:
        t["instant"], t["range"] = True, False
    else:
        t["instant"], t["range"] = False, True
    if table:
        t["format"] = "table"
    return t

def thresholds(*steps):
    out = [{"color": steps[0], "value": None}]
    for value, color in steps[1:]:
        out.append({"color": color, "value": value})
    return {"mode": "absolute", "steps": out}

def timeseries(title, description, targets, unit="", lo_hi=True, minv=None, maxv=None, line_at=None):
    custom = {"drawStyle": "line", "lineInterpolation": "linear", "lineWidth": 2, "fillOpacity": 0,
              "showPoints": "auto", "pointSize": 4, "spanNulls": True, "axisPlacement": "auto"}
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "unit": unit}
    if minv is not None: defaults["min"] = minv
    if maxv is not None: defaults["max"] = maxv
    overrides = []
    if lo_hi:
        overrides.append({"matcher": {"id": "byRegexp", "options": "/ (lo|hi)$/"},
                          "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}},
                                         {"id": "custom.lineWidth", "value": 1},
                                         {"id": "custom.showPoints", "value": "never"}]})
    if line_at is not None:
        custom["thresholdsStyle"] = {"mode": "line"}
        defaults["thresholds"] = thresholds("red", (line_at, "green"))
    return {"type": "timeseries", "title": title, "description": description, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": overrides},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
                        "tooltip": {"mode": "multi", "sort": "none"}}}

def stat(title, description, targets, unit="", steps=None, text_mode="value_and_name", mappings=None, decimals=None, color_mode="value"):
    defaults = {"color": {"mode": "thresholds"}, "unit": unit,
                "thresholds": steps or thresholds("text"), "mappings": mappings or []}
    if decimals is not None: defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": description, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                        "orientation": "auto", "textMode": text_mode, "colorMode": color_mode,
                        "graphMode": "none", "justifyMode": "auto", "wideLayout": True, "showPercentChange": False}}

def bargauge(title, description, targets, unit="", steps=None, minv=None, maxv=None, decimals=None):
    defaults = {"color": {"mode": "thresholds"}, "unit": unit, "thresholds": steps or thresholds("blue")}
    if minv is not None: defaults["min"] = minv
    if maxv is not None: defaults["max"] = maxv
    if decimals is not None: defaults["decimals"] = decimals
    return {"type": "bargauge", "title": title, "description": description, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                        "orientation": "horizontal", "displayMode": "basic", "valueMode": "color",
                        "namePlacement": "auto", "showUnfilled": True, "sizing": "auto", "minVizWidth": 8, "minVizHeight": 16, "maxVizHeight": 300}}

def to_table(renames, exclude=(), sort_field=None, numeric=()):
    """Instant table queries → one row per label set, one column per query."""
    tf = [{"id": "labelsToFields", "options": {"mode": "columns"}}]
    tf.append({"id": "merge", "options": {}})
    if numeric:
        tf.append({"id": "convertFieldType", "options": {"fields": {}, "conversions": [
            {"targetField": f, "destinationType": "number"} for f in numeric]}})
    if sort_field:
        tf.append({"id": "sortBy", "options": {"fields": {}, "sort": [{"field": sort_field, "desc": False}]}})
    ex = {"Time": True}
    for e in exclude: ex[e] = True
    tf.append({"id": "organize", "options": {"excludeByName": ex, "indexByName": {}, "includeByName": {}, "renameByName": renames}})
    return tf

def table(title, description, targets, transformations, overrides=None):
    return {"type": "table", "title": title, "description": description, "targets": targets,
            "transformations": transformations,
            "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False, "filterable": True},
                                         "thresholds": thresholds("text")}, "overrides": overrides or []},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""}, "sortBy": []}}

def barchart(title, description, targets, transformations, x_field, unit="", steps=None, line_at=None, minv=None, maxv=None):
    custom = {"lineWidth": 1, "fillOpacity": 80, "gradientMode": "none", "axisPlacement": "auto", "axisLabel": "", "hideFrom": {"legend": False, "tooltip": False, "viz": False}}
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "unit": unit}
    if minv is not None: defaults["min"] = minv
    if maxv is not None: defaults["max"] = maxv
    if line_at is not None:
        custom["thresholdsStyle"] = {"mode": "line"}
        defaults["thresholds"] = thresholds("red", (line_at, "green"))
    elif steps:
        defaults["thresholds"] = steps
    return {"type": "barchart", "title": title, "description": description, "targets": targets,
            "transformations": transformations,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"xField": x_field, "orientation": "auto", "barWidth": 0.8, "groupWidth": 0.7, "barRadius": 0,
                        "stacking": "none", "showValue": "auto", "xTickLabelRotation": 0, "xTickLabelMaxLength": 24, "xTickLabelSpacing": 0, "fullHighlight": False,
                        "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
                        "tooltip": {"mode": "multi", "sort": "none"}, "text": {}}}

RUNS_STAT = lambda: stat(
    "How many runs per task stand behind every number here?",
    "The engine's runs advisory: runs at the thinnest task, and its tier as the name. Amber below the 8-run floor for structured tool-use tasks; open-ended reasoning wants 32+. Below the floor every interval on this page is wide by construction: read an overlap as 'these runs do not separate them', never as 'they are equal'.",
    [target("agentdiff_runs_per_task_min", "A", "{{level}}", instant=True)],
    steps=thresholds("orange", (8, "green"), (32, "dark-green")), decimals=0)

VERDICT_MAP = [{"type": "value", "options": {
    "2": {"text": "improved", "color": "green", "index": 0},
    "1": {"text": "traded", "color": "yellow", "index": 1},
    "0": {"text": "flat", "color": "text", "index": 2},
    "-1": {"text": "regressed", "color": "orange", "index": 3},
    "-2": {"text": "overfit", "color": "dark-orange", "index": 4},
    "-3": {"text": "forgot", "color": "red", "index": 5},
    "-4": {"text": "gamed", "color": "dark-red", "index": 6},
}}]

# ------------------------------------------------------------------ Agents
d = D("agentdiff-agents", "AgentDiff · Agents",
      "Which agent passes which task, at what cost, and can the runs recorded tell? Every rate carries its Wilson interval; the paired line carries its interval; the advisory says whether the run count supports any of it. " + SYN,
      ["agentdiff", "agents"])
d.add(RUNS_STAT(), 0, 0, 6, 5)
d.add(stat("How many tasks did the paired inference use?",
           "Tasks both agents ran; the sign test needs at least ten to call a difference distinguishable, whatever p says.",
           [target("agentdiff_paired_tasks", "A", "{{from}} vs {{to}}", instant=True)], steps=thresholds("orange", (10, "green")), decimals=0), 6, 0, 6, 5)
d.add(stat("Is the paired difference distinguishable from zero (sign test p)?",
           "Two-sided sign test over the discordant tasks. Green below 0.05 only when the pair count clears ten; the panel to the left says whether it does.",
           [target("agentdiff_paired_sign_test_p", "A", "{{from}} − {{to}}", instant=True)], steps=thresholds("green", (0.05, "orange")), decimals=3), 12, 0, 6, 5)
d.add(stat("What is each agent's pooled pass rate, with its interval?",
           "Successes over every run of every task, and the Wilson bounds; pooling hides which task carries the failures, so read the table below with it. " + WILSON,
           [target("agentdiff_agent_pass_rate", "A", "{{agent}}", instant=True),
            target("agentdiff_agent_pass_rate_lo", "B", "{{agent}} lo", instant=True),
            target("agentdiff_agent_pass_rate_hi", "C", "{{agent}} hi", instant=True)],
           unit="percentunit", steps=thresholds("text"), decimals=2), 18, 0, 6, 5)
d.add(table("What is each agent's pass rate on each task, with its interval and the runs behind it?",
            "One row per agent and task: passes over runs, the Wilson bounds, and the mean spend per run. " + WILSON + " Cost 0 means unrecorded, and unrecorded is not free.",
            [target("agentdiff_pass_rate", "A", table=True), target("agentdiff_pass_rate_lo", "B", table=True),
             target("agentdiff_pass_rate_hi", "C", table=True), target("agentdiff_runs", "D", table=True),
             target("agentdiff_passes", "E", table=True), target("agentdiff_steps_mean", "F", table=True),
             target("agentdiff_seconds_mean", "G", table=True), target("agentdiff_tokens_mean", "H", table=True),
             target("agentdiff_cost_usd_mean", "I", table=True)],
            to_table({"Value #A": "pass rate", "Value #B": "lo", "Value #C": "hi", "Value #D": "runs", "Value #E": "passes",
                      "Value #F": "steps / run", "Value #G": "seconds / run", "Value #H": "tokens / run", "Value #I": "cost USD / run"}),
            overrides=[{"matcher": {"id": "byName", "options": "pass rate"},
                        "properties": [{"id": "unit", "value": "percentunit"}, {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
                                       {"id": "min", "value": 0}, {"id": "max", "value": 1}, {"id": "color", "value": {"mode": "continuous-RdYlGr"}}]},
                       {"matcher": {"id": "byRegexp", "options": "/^(lo|hi)$/"}, "properties": [{"id": "unit", "value": "percentunit"}]}]), 0, 5, 24, 10)
d.add(timeseries("Where does each agent's pass rate on each task sit, with its interval (dashed)?",
                 "Three series per agent and task: the rate, and its lower and upper Wilson bounds dashed. Flat over time because the file is the state of one analysis; two analyses scraped in sequence draw a step. " + WILSON,
                 [target("agentdiff_pass_rate", "A", "{{agent}} · {{task}}"),
                  target("agentdiff_pass_rate_lo", "B", "{{agent}} · {{task}} lo"),
                  target("agentdiff_pass_rate_hi", "C", "{{agent}} · {{task}} hi")], unit="percentunit", minv=0, maxv=1), 0, 15, 12, 9)
d.add(timeseries("How does the paired difference in success sit against zero, with its interval?",
                 "Mean over paired tasks of (from agent's success − to agent's success), with a normal 95% interval from its standard error over the tasks paired (dashed). The line at 0 is no difference. Under ten pairs the sign test cannot call it either way.",
                 [target("agentdiff_paired_diff", "A", "{{from}} − {{to}}"),
                  target("agentdiff_paired_diff_lo", "B", "{{from}} − {{to}} lo"),
                  target("agentdiff_paired_diff_hi", "C", "{{from}} − {{to}} hi")], line_at=0), 12, 15, 12, 9)
d.add(bargauge("How many seconds does a run take, per agent and task?", "Mean recorded latency per run: the sum of step latencies as recorded, not wall clock.",
               [target("agentdiff_seconds_mean", "A", "{{agent}} · {{task}}", instant=True)], unit="s", decimals=1), 0, 24, 6, 9)
d.add(bargauge("How many steps does a run take?", "Mean steps per run over the runs recorded.",
               [target("agentdiff_steps_mean", "A", "{{agent}} · {{task}}", instant=True)], decimals=1), 6, 24, 6, 9)
d.add(bargauge("How many tokens does a run spend?", "Mean tokens per run as the trace recorded them.",
               [target("agentdiff_tokens_mean", "A", "{{agent}} · {{task}}", instant=True)], decimals=0), 12, 24, 6, 9)
d.add(bargauge("What does a run cost, in USD (0 = unrecorded)?", "Mean cost per run as recorded. A zero is an unrecorded cost, not a free run.",
               [target("agentdiff_cost_usd_mean", "A", "{{agent}} · {{task}}", instant=True)], unit="currencyUSD", decimals=4), 18, 24, 6, 9)
d.add(bargauge("How many seconds per run went to wasted steps?", "Seconds on steps the reading marks as wasted (no information, repeat, dead end, error, spent after the answer's basis); a reading of the steps, not a measurement of the model.",
               [target("agentdiff_wasted_seconds_mean", "A", "{{agent}} · {{task}}", instant=True)], unit="s", decimals=1), 0, 33, 12, 9)
d.add(bargauge("How many tool calls returned an error, per run?", "Mean tool errors per run; a count over the steps.",
               [target("agentdiff_tool_errors_mean", "A", "{{agent}} · {{task}}", instant=True)], decimals=2), 12, 33, 12, 9)
DASH = [d]

# ------------------------------------------------------------------ Tools
d = D("agentdiff-tools", "AgentDiff · Tools",
      "Which tool did each agent lean on, and where did it burn time? Counts and sums over the pair reports' tool profiles (one representative pair per task in a runs layout), never over runs the reports do not hold. " + SYN,
      ["agentdiff", "tools"])
d.add(table("How did each agent use each tool: calls, errors, repeats, wasted calls, seconds wasted, longest identical run?",
            "Every column is a count or a sum over the pair reports in the directory; the longest identical run is a maximum over reports, not a sum.",
            [target("agentdiff_tool_calls", "A", table=True), target("agentdiff_tool_errors", "B", table=True),
             target("agentdiff_tool_repeats", "C", table=True), target("agentdiff_tool_wasted_calls", "D", table=True),
             target("agentdiff_tool_wasted_seconds", "E", table=True), target("agentdiff_tool_seconds", "F", table=True),
             target("agentdiff_tool_max_identical_run", "G", table=True)],
            to_table({"Value #A": "calls", "Value #B": "errors", "Value #C": "repeats", "Value #D": "wasted calls",
                      "Value #E": "wasted s", "Value #F": "seconds", "Value #G": "longest identical run"})), 0, 0, 24, 10)
d.add(bargauge("How many times did each agent call each tool?", "Calls of the tool by the agent, summed over the pair reports.",
               [target("agentdiff_tool_calls", "A", "{{agent}} · {{tool}}", instant=True)], decimals=0), 0, 10, 8, 10)
d.add(bargauge("How many calls returned an error?", "Calls that returned an error, summed over the pair reports.",
               [target("agentdiff_tool_errors", "A", "{{agent}} · {{tool}}", instant=True)], steps=thresholds("green", (1, "orange"), (5, "red")), decimals=0), 8, 10, 8, 10)
d.add(bargauge("How many calls repeated an input already sent?", "Calls whose input the agent had already sent to the same tool, summed over the pair reports.",
               [target("agentdiff_tool_repeats", "A", "{{agent}} · {{tool}}", instant=True)], steps=thresholds("green", (1, "orange"), (5, "red")), decimals=0), 16, 10, 8, 10)
d.add(bargauge("How many seconds went to wasted calls of each tool?", "Seconds the agent waited on calls the reading marks as wasted; a reading of the steps, not a measurement of the tool.",
               [target("agentdiff_tool_wasted_seconds", "A", "{{agent}} · {{tool}}", instant=True)], unit="s", decimals=1), 0, 20, 12, 10)
d.add(stat("What is the longest run of identical calls, and who made it?",
           "The three longest runs of consecutive identical calls to one tool in any one pair report; a retry loop shows here before it shows in the cost.",
           [target("topk(3, agentdiff_tool_max_identical_run)", "A", "{{agent}} · {{tool}}", instant=True)],
           steps=thresholds("green", (2, "orange"), (4, "red")), decimals=0), 12, 20, 12, 10)
DASH.append(d)

# ------------------------------------------------------------------ Training ground
d = D("agentdiff-training", "AgentDiff · Training ground",
      "Is the new policy better than the old, and would the runs recorded know? IQM and mean return with their intervals, the probability of improvement with its interval and the coin-flip line, the per-task deltas that an average hides, the reward audit and the critic. " + SYN,
      ["agentdiff", "training", "rl"])
d.add(RUNS_STAT(), 0, 0, 6, 6)
d.add(timeseries("What is each policy's IQM return, with its interval (dashed)?",
                 "The interquartile mean of episode returns: the mean of the middle half, the least moved by one exceptional episode. " + BOOT + " Overlapping intervals mean these runs do not separate the policies, not that they are equal.",
                 [target("agentdiff_iqm", "A", "{{agent}}"), target("agentdiff_iqm_lo", "B", "{{agent}} lo"), target("agentdiff_iqm_hi", "C", "{{agent}} hi")]), 6, 0, 9, 9)
d.add(timeseries("How often does a random run of the new policy beat one of the old, with its interval and the coin flip?",
                 "Probability of improvement: within-task Mann-Whitney, ties half, averaged over tasks; the line at 0.5 is the coin flip. " + BOOT + " It is an average over tasks; the per-task panel below shows where it falls under 0.5.",
                 [target("agentdiff_improvement", "A", "{{to}} > {{from}}"), target("agentdiff_improvement_lo", "B", "{{to}} > {{from}} lo"), target("agentdiff_improvement_hi", "C", "{{to}} > {{from}} hi")],
                 unit="percentunit", minv=0, maxv=1, line_at=0.5), 15, 0, 9, 9)
d.add(timeseries("What is each policy's mean return, with its interval (dashed)?",
                 "The mean return over every episode of the policy. " + BOOT + " Recorded rewards, or shaped from the labels when the source label says shaped.",
                 [target("agentdiff_return_mean", "A", "{{agent}} ({{source}})"), target("agentdiff_return_mean_lo", "B", "{{agent}} lo"), target("agentdiff_return_mean_hi", "C", "{{agent}} hi")]), 0, 9, 12, 9)
d.add(bargauge("Per task: does the new policy beat the old (0.5 = coin flip)?",
               "The probability of improvement on each task alone: the to policy's runs against the from policy's, ties half. Red below 0.5 is a task the new policy is worse on: a regression the average hides.",
               [target("agentdiff_task_improvement", "A", "{{task}}", instant=True)], unit="percentunit", steps=thresholds("red", (0.5, "green")), minv=0, maxv=1, decimals=2), 12, 9, 12, 9)
d.add(bargauge("Per task: by how much does the mean return differ (new minus old)?",
               "A difference of two per-task means over the runs recorded; it carries no interval of its own, so read it with the panel to the left.",
               [target("agentdiff_task_return_delta", "A", "{{task}}", instant=True)], steps=thresholds("red", (0, "green")), decimals=2), 0, 18, 8, 9)
d.add(stat("How many (failed, passed) episode pairs does the reward order the wrong way round?",
           "The reward audit's disagreement count in each scope: pooled across tasks, by task, and shaping (the return with the last step removed, where a policy would collect return without passing). Signals to investigate, not proven defects.",
           [target("agentdiff_reward_disagreements", "A", "{{scope}}", instant=True)], steps=thresholds("green", (1, "orange"), (10, "red")), decimals=0), 8, 18, 8, 9)
d.add(stat("How much of the return-to-go does the critic explain?",
           "Explained variance of the value head against the realised discounted return-to-go, over the steps that carry a value. Negative means a constant equal to the mean would have done better.",
           [target("agentdiff_critic_explained_variance", "A", "{{agent}}", instant=True)], steps=thresholds("red", (0, "orange"), (0.5, "green")), decimals=2), 16, 18, 8, 9)
d.add(stat("How well does the return rank the episodes by outcome?",
           "Spearman rank correlation between return and outcome; the binary outcome's ties cap it below 1. The shaping basis removes the last step's reward, which agrees with the outcome by construction.",
           [target("agentdiff_reward_rank_agreement", "A", "{{basis}}", instant=True)], steps=thresholds("red", (0.3, "orange"), (0.7, "green")), decimals=2), 0, 27, 12, 6)
d.add(stat("How many pairs did each scope compare?", "The denominators of the disagreement counts.",
           [target("agentdiff_reward_disagreement_pairs", "A", "{{scope}}", instant=True)], decimals=0), 12, 27, 12, 6)
DASH.append(d)

# ------------------------------------------------------------------ Evolution
GEN_TABLE = lambda renames: to_table(renames, exclude=("family", "synthetic"), sort_field="index", numeric=("index",))
d = D("agentdiff-evolution", "AgentDiff · Evolution",
      "Did each step of a self-evolving agent's lineage help, and which generation should be kept? Per generation the IQM with its interval in lineage order, the pass rate, the artifact sizes against their budget; per step the verdict, the probability of improvement with its interval, and the protected paths touched. " + SYN,
      ["agentdiff", "evolution"])
d.add(stat("Which generation has the highest IQM?", "The generation the engine ranks first, and its IQM (task-balanced when the section carries one). Read it beside the intervals below: the next generation's interval usually overlaps.",
           [target("agentdiff_evolution_best", "A", "{{generation}}", instant=True)], decimals=2, color_mode="none"), 0, 0, 6, 5)
d.add(stat("Which generation does the engine recommend keeping?", "The best generation, unless a later one's interval clears it; never a generation whose incoming step was gamed. The name says whether it is the last one.",
           [target("agentdiff_evolution_recommended", "A", "{{generation}} · last: {{is_last}}", instant=True)], decimals=2, color_mode="none"), 6, 0, 6, 5)
d.add(RUNS_STAT(), 12, 0, 6, 5)
d.add(stat("How many steps earned each verdict?", "Counts over the lineage's steps; exactly one verdict per step.",
           [target("agentdiff_evolution_verdicts", "A", "{{verdict}}", instant=True)], decimals=0, color_mode="none"), 18, 0, 6, 5)
d.add(barchart("How did the IQM move across the lineage, with its interval?",
               "Three bars per generation: the pooled IQM and its lower and upper bounds. " + BOOT + " Generations are ordered by the parent chain, not by time.",
               [target("agentdiff_evolution_iqm", "A", table=True), target("agentdiff_evolution_iqm_lo", "B", table=True), target("agentdiff_evolution_iqm_hi", "C", table=True)],
               GEN_TABLE({"Value #A": "IQM", "Value #B": "lo", "Value #C": "hi"}), "generation"), 0, 5, 12, 9)
d.add(barchart("How did the task-balanced IQM move across the lineage, with its interval?",
               "The mean over tasks of each task's IQM, so a task lost outright is not trimmed away as the pooled IQM's lower tail; what the engine ranks generations by. " + BOOT,
               [target("agentdiff_evolution_iqm_balanced", "A", table=True), target("agentdiff_evolution_iqm_balanced_lo", "B", table=True), target("agentdiff_evolution_iqm_balanced_hi", "C", table=True)],
               GEN_TABLE({"Value #A": "IQM (task-balanced)", "Value #B": "lo", "Value #C": "hi"}), "generation"), 12, 5, 12, 9)
d.add({**timeseries("What verdict did each step earn?",
                    "One row per step (parent → child), coloured by the verdict: improved, traded, flat, regressed, overfit, forgot, gamed. Exactly one verdict per step; the flags a step carries beside it are in the table below.",
                    [target("agentdiff_evolution_step_verdict_code", "A", "{{from}} → {{to}}")], lo_hi=False),
        "type": "state-timeline",
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "custom": {"lineWidth": 0, "fillOpacity": 70}, "mappings": VERDICT_MAP,
                                     "thresholds": thresholds("text")}, "overrides": []},
        "options": {"mergeValues": True, "showValue": "always", "alignValue": "center", "rowHeight": 0.8,
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []}, "tooltip": {"mode": "single", "sort": "none"}}}, 0, 14, 24, 7)
d.add(barchart("How did the pass rate move across the lineage?", "Passes over episodes of the generation; a proportion of a handful of runs per task, read it with the advisory.",
               [target("agentdiff_evolution_pass_rate", "A", table=True)], GEN_TABLE({"Value #A": "pass rate", "Value": "pass rate"}), "generation", unit="percentunit", minv=0, maxv=1), 0, 21, 12, 8)
d.add(barchart("Did each step's child beat its parent, with its interval and the coin flip?",
               "Three bars per step: the probability that a random episode of the child beats one of the parent on the same task, and its bounds. " + BOOT + " The line at 0.5 is the coin flip.",
               [target('label_join(agentdiff_evolution_step_improvement, "edge", " → ", "from", "to")', "A", table=True),
                target('label_join(agentdiff_evolution_step_improvement_lo, "edge", " → ", "from", "to")', "B", table=True),
                target('label_join(agentdiff_evolution_step_improvement_hi, "edge", " → ", "from", "to")', "C", table=True)],
               to_table({"Value #A": "P(improve)", "Value #B": "lo", "Value #C": "hi"}, exclude=("family", "synthetic", "from", "to"), sort_field="step", numeric=("step",)),
               "edge", unit="percentunit", line_at=0.5, minv=0, maxv=1), 12, 21, 12, 8)
for i, (what, title) in enumerate((("prompt_chars", "Is the prompt growing past its budget?"), ("rules", "Are the rules growing past their budget?"), ("memory", "Is the memory growing past its budget?"))):
    d.add(barchart(title, "The artifact's size in each generation beside the budget lineage.json set; exceeding it is a finding, not an error. Unbounded growth is one of the ways evolution goes wrong.",
                   [target(f"agentdiff_evolution_{what}", "A", table=True),
                    target(f'agentdiff_evolution_{what} * 0 + on() group_left() agentdiff_evolution_budget{{what="{what}"}}', "B", table=True)],
                   GEN_TABLE({"Value #A": what.replace("_", " "), "Value #B": "budget"}), "generation"), i * 8, 29, 8, 8)
d.add(table("Which protected paths did a step touch?",
            "A protected path is one lineage.json named: the agent's own verifier, grader, reward config. A weakened row is the agent editing the thing that judges it; a restored row is a later step putting it back. The source column says whether the diff or the episodes showed it.",
            [target("agentdiff_evolution_protected_touched", "A", table=True)],
            to_table({"Value #A": "touched", "Value": "touched"}, exclude=("family", "synthetic", "Value #A", "Value"), sort_field="step", numeric=("step",))), 0, 37, 12, 8)
d.add(table("Which flags does each step carry?", "Checks that fired beside the verdict: overfit, protected, over_budget, collapsed, noisy (accepted on an interval that spans the coin flip), axes_disagree.",
            [target("agentdiff_evolution_step_flag", "A", table=True)],
            to_table({}, exclude=("family", "synthetic", "Value #A", "Value"), sort_field="step", numeric=("step",))), 12, 37, 12, 8)
d.add(barchart("Pass rate per generation and task: where did a step forget a task?",
               "Each generation's pass rate on each task; a task whose bar falls while the others rise is the forgetting an average hides.",
               [target("agentdiff_evolution_task_pass_rate", "A", "{{generation}} · {{task}}", table=True)],
               to_table({"Value #A": "pass rate", "Value": "pass rate"}, exclude=("family", "synthetic", "index"), sort_field="generation") + [
                   {"id": "groupingToMatrix", "options": {"columnField": "task", "rowField": "generation", "valueField": "pass rate", "emptyValue": "null"}}],
               "generation\\task", unit="percentunit", minv=0, maxv=1), 0, 45, 24, 9)
DASH.append(d)

# ------------------------------------------------------------------ One run
STEP_TABLE = lambda renames: to_table(renames, exclude=("agent", "task", "run", "synthetic"), sort_field="step", numeric=("step",))
d = D("agentdiff-run", "AgentDiff · One run",
      "What did this run earn, step by step, and where did the time go? From `agentdiff grafana trace.json`: reward and return so far per step (recorded rewards only; a shaped reward needs the pair report), latency and tokens per step. " + SYN,
      ["agentdiff", "run"])
d.add(stat("Did the run succeed?", "outcome.success as graded, not as judged.",
           [target("agentdiff_run_success", "A", "{{agent}} · {{task}} · {{run}}", instant=True)],
           mappings=[{"type": "value", "options": {"1": {"text": "passed", "color": "green", "index": 0}, "0": {"text": "failed", "color": "red", "index": 1}}}],
           steps=thresholds("red", (1, "green"))), 0, 0, 8, 5)
d.add(stat("What did it earn (the return)?", "The sum of recorded step rewards; absent when the trace recorded none.",
           [target("agentdiff_run_return", "A", "{{agent}} · {{task}} · {{run}}", instant=True)], decimals=2, color_mode="none"), 8, 0, 4, 5)
d.add(stat("How many steps?", "A count.", [target("agentdiff_run_steps", "A", "{{run}}", instant=True)], decimals=0, text_mode="value", color_mode="none"), 12, 0, 3, 5)
d.add(stat("How many seconds?", "The sum of step latencies as recorded.", [target("agentdiff_run_seconds", "A", "{{run}}", instant=True)], unit="s", decimals=1, text_mode="value", color_mode="none"), 15, 0, 3, 5)
d.add(stat("How many tokens?", "As the trace recorded them.", [target("agentdiff_run_tokens", "A", "{{run}}", instant=True)], decimals=0, text_mode="value", color_mode="none"), 18, 0, 3, 5)
d.add(stat("How many tool errors?", "Tool calls that returned an error.", [target("agentdiff_run_tool_errors", "A", "{{run}}", instant=True)], steps=thresholds("green", (1, "orange"), (3, "red")), decimals=0, text_mode="value"), 21, 0, 3, 5)
d.add(barchart("What was paid at each step?", "What the environment paid at each step, as recorded; a step without a reward earned 0.",
               [target("agentdiff_step_reward", "A", table=True)], STEP_TABLE({"Value #A": "reward", "Value": "reward"}) + [
                   {"id": "organize", "options": {"excludeByName": {"type": True, "name": True}, "indexByName": {}, "includeByName": {}, "renameByName": {}}}], "step", line_at=0), 0, 5, 12, 9)
d.add(barchart("How did the return accumulate, step by step?", "The running sum of recorded rewards up to and including the step.",
               [target("agentdiff_step_return_cum", "A", table=True)], STEP_TABLE({"Value #A": "return so far", "Value": "return so far"}) + [
                   {"id": "organize", "options": {"excludeByName": {"type": True, "name": True}, "indexByName": {}, "includeByName": {}, "renameByName": {}}}], "step", line_at=0), 12, 5, 12, 9)
d.add(barchart("Where did the seconds go, step by step?", "As recorded per step; a tall bar is a step the run waited on.",
               [target("agentdiff_step_seconds", "A", table=True)], STEP_TABLE({"Value #A": "seconds", "Value": "seconds"}) + [
                   {"id": "organize", "options": {"excludeByName": {"type": True, "name": True}, "indexByName": {}, "includeByName": {}, "renameByName": {}}}], "step", unit="s"), 0, 14, 12, 9)
d.add(barchart("Where did the tokens go, step by step?", "As recorded per step.",
               [target("agentdiff_step_tokens", "A", table=True)], STEP_TABLE({"Value #A": "tokens", "Value": "tokens"}) + [
                   {"id": "organize", "options": {"excludeByName": {"type": True, "name": True}, "indexByName": {}, "includeByName": {}, "renameByName": {}}}], "step"), 12, 14, 12, 9)
d.add(table("What did each step do: type, name, reward, seconds, tokens, value?",
            "One row per step in order. Value and advantage columns are empty when the trace carries none.",
            [target("agentdiff_step_reward", "A", table=True), target("agentdiff_step_return_cum", "B", table=True),
             target("agentdiff_step_seconds", "C", table=True), target("agentdiff_step_tokens", "D", table=True),
             target("agentdiff_step_value", "E", table=True), target("agentdiff_step_advantage", "F", table=True)],
            STEP_TABLE({"Value #A": "reward", "Value #B": "return so far", "Value #C": "seconds", "Value #D": "tokens", "Value #E": "value", "Value #F": "advantage"})), 0, 23, 24, 12)
DASH.append(d)


# ---------------------------------------------------------------- evals: the eval that evolves with the lineage
COV_TABLE = lambda renames, sort_field, numeric=(): to_table(renames, exclude=("family", "synthetic"), sort_field=sort_field, numeric=numeric)
d = D("agentdiff-evals", "AgentDiff · Evals",
      "The eval that evolves beside a self-evolving agent: its own lineage of metrics, every candidate it tested with the decision and the reason, what it flags with hindsight, and its own integrity. " + SYN,
      ["agentdiff", "coevolution"])
d.add(stat("How many generations has the eval grown?",
           "e0 is the base metrics; one more generation per agent step that taught the eval a metric. An eval that never grows learned nothing from this lineage, which is a finding about the lineage as much as about the eval.",
           [target("agentdiff_coevolution_eval_generations", "A", "{{family}}", instant=True)], decimals=0, color_mode="none"), 0, 0, 6, 5)
d.add(stat("How many candidates did it test, and keep?",
           "Every candidate is a ledger row, adopted or rejected; the rejected ones and their reasons are in the table below. The eval tests many and keeps few because every candidate must clear a Bonferroni-adjusted level over the candidates tested at its step.",
           [target("agentdiff_coevolution_candidates", "A", "{{decision}}", instant=True)], decimals=0, color_mode="none"), 6, 0, 6, 5)
d.add(stat("Do the base eval and the evolved eval keep the same generation?",
           "1 when both recommend the same generation, 0 when the evolved eval passes over a generation the base would keep because a learned, outcome-linked metric flags its incoming step.",
           [target("agentdiff_coevolution_recommended_agree", "A", "base {{base}} · evolved {{evolved}}", instant=True)], decimals=0,
           mappings=[{"type": "value", "options": {"1": {"text": "agree", "color": "green"}, "0": {"text": "disagree", "color": "orange"}}}]), 12, 0, 6, 5)
d.add(RUNS_STAT(), 18, 0, 6, 5)
d.add(table("What does every metric say on every generation, with its interval?",
            "Computed with hindsight: a learned metric is shown on the generations before it was adopted too, which is how the eval says what it would have seen. " + BOOT,
            [target("agentdiff_coevolution_metric", "A", table=True), target("agentdiff_coevolution_metric_lo", "B", table=True), target("agentdiff_coevolution_metric_hi", "C", table=True)],
            COV_TABLE({"Value #A": "value", "Value #B": "lo", "Value #C": "hi"}, "metric")), 0, 5, 12, 10)
d.add(table("What does the evolved eval flag with hindsight, step by step?",
            "A metric whose change across an agent step has an interval excluding zero in its bad direction. learned=true is a metric the eval learned; learned=false is a base metric whose interval the base verdict does not read. " + BOOT,
            [target('label_join(agentdiff_coevolution_step_flag_delta, "edge", " → ", "from", "to")', "A", table=True),
             target('label_join(agentdiff_coevolution_step_flag_delta_lo, "edge", " → ", "from", "to")', "B", table=True),
             target('label_join(agentdiff_coevolution_step_flag_delta_hi, "edge", " → ", "from", "to")', "C", table=True)],
            COV_TABLE({"Value #A": "delta", "Value #B": "lo", "Value #C": "hi"}, "step", numeric=("step",))), 12, 5, 12, 10)
d.add(table("Which candidates were adopted, and which rejected, by what?",
            "The eval's ledger: every candidate a probe proposed, at which agent step, and the validators it failed (computable, informative, distinct, linked, not_already); empty when adopted. A rejection is a finding about the data, never hidden.",
            [target("agentdiff_coevolution_candidate", "A", table=True)],
            COV_TABLE({"Value #A": "row", "Value": "row"}, "index", numeric=("index",))), 0, 15, 14, 10)
d.add(barchart("Which probe proposed, and how much of it survived?",
               "Candidates by the probe that proposed them and the decision they earned. A probe that proposes much and keeps little is asking a question this lineage does not answer at this sample size.",
               [target("agentdiff_coevolution_candidates_by_probe", "A", "{{probe}} · {{decision}}", table=True)],
               COV_TABLE({"Value #A": "candidates", "Value": "candidates"}, "probe"), "probe", unit="", minv=0), 14, 15, 10, 10)
d.add(bargauge("How many steps late was each learned metric?",
               "Agent steps between the first step a learned metric would have flagged and the step it was adopted at. 0 means the eval learned it at the first step it flags; a larger lag is what hindsight bought.",
               [target("agentdiff_coevolution_hindsight_lag", "A", "{{metric}}", instant=True)], decimals=0, minv=0), 0, 25, 12, 6)
d.add(stat("How far has the eval drifted from its base, and how strict was it?",
           "Jaccard distance of the final active metric set from the base set, and the smallest adjusted level at which a candidate was tested. Base metrics are never retired, so drift measures growth.",
           [target("agentdiff_coevolution_drift", "A", "drift from base", instant=True),
            target("agentdiff_coevolution_min_adjusted_alpha", "B", "smallest adjusted alpha", instant=True)], decimals=4, color_mode="none"), 12, 25, 6, 6)
d.add(stat("How many loop closures?",
           "A step a metric flagged, followed by a later step on which the lineage recovered on the same metric: recovered, not attributed. kind=learned counts closures on metrics the eval learned.",
           [target("agentdiff_coevolution_closures", "A", "{{kind}}", instant=True)], decimals=0, color_mode="none"), 18, 25, 6, 6)
DASH.append(d)

OUT.mkdir(parents=True, exist_ok=True)
names = {"agentdiff-agents": "agents", "agentdiff-tools": "tools", "agentdiff-training": "training", "agentdiff-evolution": "evolution", "agentdiff-run": "run", "agentdiff-evals": "evals"}
for dash in DASH:
    path = OUT / (names[dash.uid] + ".json")
    path.write_text(json.dumps(dash.build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(path, len(dash.panels), "panels")
