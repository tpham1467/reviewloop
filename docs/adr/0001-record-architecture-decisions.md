# ADR 0001: Record architecture decisions

Status: accepted. Date: 2026-11-27.

## Context

reviewloop makes decisions whose reasons are not visible in the code: which agent CLIs are supported, how they are
restricted, where state lives. Contributors need the reasons to change them safely.

## Decision

Record each significant decision as an Architecture Decision Record in `docs/adr/NNNN-short-title.md`, numbered in
order. Each record has a status, a date, the context, the decision and its consequences. A record is not edited after
it is accepted, except to mark it superseded by a later record.

## Consequences

- Reviewers can ask for an ADR when a pull request makes a decision that is hard to reverse.
- The records are part of the documentation site.
