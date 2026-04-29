#!/usr/bin/env python3
import argparse
import re
import sys
from collections import Counter, defaultdict


RULE_NO_RE = re.compile(r"^@(\d+)\s+")
LABEL_RE = re.compile(r'\blabel\s+"([^"]+)"')
STATE_RULE_RE = re.compile(r"\brule\s+(\d+)\b")


def strip_inline_comment(line):
    """
    Split a pf.conf line into:
      rule_part, description

    Only treats '# ' as a description delimiter, per your format.
    This deliberately does not treat bare '#' as a comment delimiter.
    """
    marker = "# "
    idx = line.find(marker)

    if idx == -1:
        return line.rstrip(), None

    rule_part = line[:idx].rstrip()
    description = line[idx + len(marker):].strip()

    return rule_part, description or None


def parse_rules_file(path):
    """
    Parse `pfctl -vvs rules`.

    Returns:
      rule_number -> label
    """
    rule_labels = {}
    current_rule = None

    with open(path, "r", errors="replace") as f:
        for line in f:
            m = RULE_NO_RE.search(line)

            if m:
                current_rule = int(m.group(1))

                lm = LABEL_RE.search(line)
                if lm:
                    rule_labels[current_rule] = lm.group(1)

                continue

            if current_rule is not None:
                lm = LABEL_RE.search(line)
                if lm:
                    rule_labels[current_rule] = lm.group(1)

    return rule_labels


def parse_config_descriptions(path):
    """
    Parse the pf configuration source file.

    Returns:
      label -> description
    """
    label_descriptions = {}

    with open(path, "r", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            rule_part, description = strip_inline_comment(line)

            if not description:
                continue

            lm = LABEL_RE.search(rule_part)
            if not lm:
                continue

            label = lm.group(1)

            # Keep first description if duplicate labels appear.
            # Duplicate labels are possible, but usually hash labels should be unique.
            label_descriptions.setdefault(label, description)

    return label_descriptions


def parse_states_file(path, rule_labels, label_descriptions):
    """
    Parse `pfctl -vvs states`.

    Returns:
      Counter keyed by final display name
      Counter of rule numbers without labels
      Counter of labels without config descriptions
    """
    counts = Counter()
    unlabeled_rules = Counter()
    undescribed_labels = Counter()

    with open(path, "r", errors="replace") as f:
        for line in f:
            m = STATE_RULE_RE.search(line)
            if not m:
                continue

            rule_no = int(m.group(1))
            label = rule_labels.get(rule_no)

            if not label:
                unlabeled_rules[rule_no] += 1
                continue

            description = label_descriptions.get(label)

            if description:
                display_name = description
            else:
                display_name = f"{label} [no description]"
                undescribed_labels[label] += 1

            counts[display_name] += 1

    return counts, unlabeled_rules, undescribed_labels


def print_counts(title, counts):
    print(title)
    for key, count in counts.most_common():
        print(f"{count:10d}  {key}")


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate pf states by rule label, enriched with descriptions from pf config comments."
    )

    parser.add_argument(
        "rules",
        help="Output of: pfctl -vvs rules",
    )

    parser.add_argument(
        "states",
        help="Output of: pfctl -vvs states",
    )

    parser.add_argument(
        "config",
        help="pf config source file containing labels and trailing '# description' comments",
    )

    parser.add_argument(
        "--show-hashes",
        action="store_true",
        help="Include the hash label next to the description",
    )

    args = parser.parse_args()

    rule_labels = parse_rules_file(args.rules)
    label_descriptions = parse_config_descriptions(args.config)

    counts = Counter()
    unlabeled_rules = Counter()
    undescribed_labels = Counter()

    with open(args.states, "r", errors="replace") as f:
        for line in f:
            m = STATE_RULE_RE.search(line)
            if not m:
                continue

            rule_no = int(m.group(1))
            label = rule_labels.get(rule_no)

            if not label:
                unlabeled_rules[rule_no] += 1
                continue

            description = label_descriptions.get(label)

            if description:
                if args.show_hashes:
                    display_name = f"{description} [{label}]"
                else:
                    display_name = description
            else:
                display_name = f"{label} [no description]"
                undescribed_labels[label] += 1

            counts[display_name] += 1

    print_counts("States by config description:", counts)

    if unlabeled_rules:
        print()
        print_counts("States by unlabeled rule number:", unlabeled_rules)

    if undescribed_labels:
        print()
        print_counts("States by label without config description:", undescribed_labels)


if __name__ == "__main__":
    main()
