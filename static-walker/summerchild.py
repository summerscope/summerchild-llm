"""Sweet Summer Child Score — static terminal client (LLM-era rubric).

Walks the canonical questions.json end-to-end without an agent. Useful as a
reference implementation, for non-agent users, and for verification.

For the conversational agent contract (which consumes the same questions.json),
see AGENT_CONTRACT.md.
"""

import json
import collections
import os
import sys


QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "questions.json")

with open(QUESTIONS_PATH, "r") as io:
    data = json.load(io)


State = collections.namedtuple(
    "State",
    ["multiplier", "score", "currentq", "recommendations", "depth", "answers_given"],
)

START_QUESTION = "Q-llm_depth"

start_state = State(
    multiplier=1.0,
    score=0,
    currentq=START_QUESTION,
    recommendations=[],
    depth=None,
    answers_given={},
)


def print_question(text, answers_joined, answer_keys):
    print(f"\n-----------------\n{text}\n-----------------\n")
    print(f"{answers_joined}\n")
    raw = input("> ").strip()
    if not raw:
        return None
    upper = raw.upper()
    for key in answer_keys:
        if key.upper() == upper:
            return key
    print(f"\n⚠️  Sorry, '{raw}' is not a valid answer — please try again.")
    return None


def validate_answer(text, answers_joined, answer_keys):
    answered = None
    while not answered:
        answered = print_question(text, answers_joined, answer_keys)
    return answered


def format_question(question):
    text = question["text"]
    answers = question["answers"]
    answer_lines = []
    for key, value in answers.items():
        answer_lines.append(f"{key}) {value['text']}")
    answers_joined = "\n".join(answer_lines)
    answer_key = validate_answer(text, answers_joined, list(answers.keys()))
    return answer_key, answers[answer_key]


def update_state(state, question, answer_key, answer):
    new_answers_given = dict(state.answers_given)
    new_answers_given[question["id"]] = answer_key

    new_depth = state.depth
    if "depth_set" in answer:
        new_depth = answer["depth_set"]

    recommendation = answer.get("recommendation")
    new_recommendations = state.recommendations
    if recommendation:
        new_recommendations = state.recommendations + [
            (question["id"], recommendation)
        ]

    return state._replace(
        multiplier=answer.get("multiplier", state.multiplier),
        score=state.score + answer.get("score", 0),
        currentq=answer.get("nextq", ""),
        recommendations=new_recommendations,
        depth=new_depth,
        answers_given=new_answers_given,
    )


def ask_question(question, state):
    answer_key, answer = format_question(question)
    return update_state(state, question, answer_key, answer)


def lookup_question(data, qid):
    for question in data:
        if question["id"] == qid:
            return question
    raise KeyError(f"{qid} was not found")


def gating_satisfied(question, state):
    """Check whether this question should be asked given current state.

    Gating shapes:
      - None / missing: always ask.
      - {"depth_in": [...]}: only ask if state.depth is in the list. If
        depth has not been set yet (LLM-depth question not yet answered),
        treat depth-gated questions as not yet applicable and skip.
      - {"prerequisite": "Q-id.answer == 'X'"}: only ask if the named
        prior question was answered with the given value.
    """
    gating = question.get("gating")
    if not gating:
        return True

    if "depth_in" in gating:
        if state.depth is None:
            return False
        if state.depth not in gating["depth_in"]:
            return False

    if "prerequisite" in gating:
        prereq = gating["prerequisite"]
        # Tiny expression parser: "Q-id.answer == 'value'"
        try:
            left, op, right = prereq.split(maxsplit=2)
            assert op == "=="
            q_id = left.split(".")[0]
            expected = right.strip().strip("'").strip('"')
            if state.answers_given.get(q_id) != expected:
                return False
        except (ValueError, AssertionError, KeyError):
            # Malformed prerequisite — treat as satisfied (fail open for safety)
            return True

    return True


def parse_range(range_str):
    s1, s2 = range_str.split("-")
    return int(s1), int(s2)


def render_recommendation(rec):
    """Render a recommendation, supporting both structured (new) and string (legacy) forms."""
    if isinstance(rec, str):
        return rec
    if isinstance(rec, dict):
        parts = []
        if rec.get("tradeoff"):
            parts.append(f"  Tradeoff: {rec['tradeoff']}")
        if rec.get("considerations"):
            parts.append(f"  Considerations: {rec['considerations']}")
        refs = rec.get("references", [])
        if refs:
            ref_strs = [f"{r.get('source','?')} {r.get('ref','?')}" for r in refs]
            parts.append(f"  References: {', '.join(ref_strs)}")
        return "\n".join(parts)
    return str(rec)


def print_summary(data, state):
    raw_deductions = state.score
    multiplier = state.multiplier
    amplified = multiplier * raw_deductions
    final_score = max(0, min(99, int(round(99 - amplified))))

    print("\n" + "=" * 60)
    print("Sweet Summer Child Score")
    print("=" * 60)
    print(f"\nYou earned {raw_deductions} raw risk points across the questions you answered.")
    print(f"At your cohort scale (amplifier ×{multiplier:.2f}), these become {amplified:.1f} risk points.")
    print(f"\n  Sweet Summer Child Score: {final_score} / 99\n")

    results_all = lookup_question(data, "Results")
    for _k, range_obj in results_all["results"].items():
        low, high = parse_range(range_obj["range"])
        if low <= final_score <= high:
            print(f"  {range_obj['title']}")
            print(f"  {range_obj['text']}")
            print()
            break

    print("=" * 60)
    print("Recommendations and tradeoffs flagged during this assessment")
    print("=" * 60)

    if not state.recommendations:
        print("\nNo recommendations flagged.\n")
        return

    for (q_id, rec) in state.recommendations:
        print(f"\n• [{q_id}]")
        print(render_recommendation(rec))


def advance_to_next_applicable(data, state):
    """Skip past any question whose gating is not satisfied.

    For depth-gated questions, follow the first answer's nextq as the
    skip-forward path (all answers in the new schema share the same nextq
    when the question is purely additive). For prereq-gated questions,
    same approach.
    """
    while state.currentq:
        question = lookup_question(data, state.currentq)
        if gating_satisfied(question, state):
            return state
        # Skip — follow the first answer's nextq
        first_answer = next(iter(question["answers"].values()))
        skip_target = first_answer.get("nextq", "")
        state = state._replace(currentq=skip_target)
    return state


def run_quiz(data, state):
    state = advance_to_next_applicable(data, state)
    while state.currentq:
        question = lookup_question(data, state.currentq)
        state = ask_question(question, state)
        state = advance_to_next_applicable(data, state)
    print_summary(data, state)


if __name__ == "__main__":
    try:
        run_quiz(data, start_state)
    except (KeyboardInterrupt, EOFError):
        print("\n\nAssessment interrupted. No score recorded.")
        sys.exit(0)
