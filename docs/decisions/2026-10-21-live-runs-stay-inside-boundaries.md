# Live runs are allowed, inside stated boundaries, for everyone who runs one

Date: 2026-10-21

> **The rule.** Anyone may run an experiment live, and every live run stays inside these boundaries:
> 1. **The target's terms.** The run is allowed by the target's licence, or for a hosted service by its terms of use. Maintainers accept no new terms and sign up for no account or paid plan without the project owner's approval. Nobody works around rate limits or scrapes beyond what the terms allow.
> 2. **Isolation.** Each run proves it could not see the runner's home directory, credentials, other repositories, session logs or connected services. No recorded result contains anything from the runner's machine.
> 3. **Spend.** Each experiment declares `spend_cap_usd` before its first call, prices every call type in advance, and the runner stops at the cap.
> 4. **Attribution.** Each experiment with live results records `launched_by`: whoever ran it and paid for it.
> 5. **No outreach.** Running an experiment never opens an issue, sends a message or posts to a target's authors or community. Any such contact is a separate, deliberate act by a person.
> 6. **Order.** The protocol is committed before the run, and the gate never makes a live call.

## Why

The repository is meant to be repeated by people other than its authors, and repeating an experiment on AI tools means running them on your own account. These boundaries protect three parties:

- **the target's authors:** their terms are followed, and they are not contacted by automation;
- **the person running it:** their machine's private data stays out of the run and out of the record, and spend has a hard cap;
- **the record:** every result says who produced it, under which protocol.

Boundaries 2 to 4 and 6 can be checked from the files, and the gate checks them. Boundaries 1 and 5 are checked in review.

Amended 2026-10-21: the gate checks boundaries 2–4 and 6 per run folder under `recorded/runs/<run>/`; a result outside a run folder is not attributed to a live run.
