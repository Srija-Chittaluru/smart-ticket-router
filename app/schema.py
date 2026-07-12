CATEGORIES = [
    "Technical Issue",
    "Billing & Payments",
    "Account Access",
    "Bug Report",
    "Feature Request",
    "General Inquiry",
]

PRIORITIES = ["High", "Medium", "Low"]

TEAMS = [
    "Tier 1 Support",
    "Tier 2 Engineering",
    "Billing Team",
    "Account & Security Team",
    "Product Team",
    "Customer Success",
]

TICKET_JSON_SCHEMA = {
    "name": "ticket_routing",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "priority": {"type": "string", "enum": PRIORITIES},
            "assigned_team": {"type": "string", "enum": TEAMS},
            "reasoning": {
                "type": "string",
                "description": (
                    "One sentence explaining the classification, referencing "
                    "the specific detail in the message that drove the "
                    "category/priority/team choice."
                ),
            },
            "clarification_needed": {
                "type": "boolean",
                "description": (
                    "True if the message is too short/vague to confidently "
                    "classify and a human should ask the customer for more "
                    "detail before final routing."
                ),
            },
        },
        "required": [
            "category",
            "priority",
            "assigned_team",
            "reasoning",
            "clarification_needed",
        ],
        "additionalProperties": False,
    },
}

REQUIRED_FIELDS = ["category", "priority", "assigned_team", "reasoning"]

SYSTEM_PROMPT = f"""You are a support ticket triage assistant for a software product's helpdesk.

Given a single raw customer support message, classify it and return ONLY the
structured fields defined by the JSON schema. Do not include any text
outside the JSON.

CATEGORIES (choose exactly one): {", ".join(CATEGORIES)}
PRIORITIES (choose exactly one): {", ".join(PRIORITIES)}
TEAMS (choose exactly one): {", ".join(TEAMS)}

Category guidance:
- Technical Issue: product/service isn't working as expected for the user (outages, errors, crashes, login/loading failures) when it's not clearly a code defect report.
- Bug Report: user is reporting a specific reproducible defect in the software.
- Billing & Payments: charges, invoices, refunds, subscription/payment problems.
- Account Access: login, password reset, locked/suspended accounts, permissions.
- Feature Request: user wants new or changed functionality; nothing is broken.
- General Inquiry: questions, how-to, information requests with no problem to fix.

Priority rubric (base this ONLY on described business/user impact and
urgency -- NEVER on emotional tone, punctuation, or capitalization):
- High: outage or total loss of function, security/data issue, payment
  failure blocking the customer, no workaround, or many users affected.
- Medium: partial functionality loss, a workaround exists, single user
  affected, moderate urgency.
- Low: cosmetic issues, general questions, feature requests, no urgency.
An angry or all-caps message about a trivial issue is still Low/Medium.
A calm, politely-worded message about a total outage is still High.

Team routing guidance:
- Tier 1 Support: general technical issues, how-to questions, first line triage.
- Tier 2 Engineering: confirmed bugs, complex/technical issues Tier 1 can't resolve.
- Billing Team: all billing/payment/subscription/refund issues.
- Account & Security Team: login, access, permissions, suspected security issues.
- Product Team: feature requests, product feedback.
- Customer Success: general inquiries, non-technical relationship/account questions.

Edge case handling:
1. Angry/emotional tone: strip the emotion out and classify based on the
   underlying facts stated in the message. Reasoning should cite the facts
   (e.g. "3 days of total outage"), not the tone.
2. Very short or vague messages (e.g. a single word like "broken"): do NOT
   guess wildly. Set clarification_needed=true, choose the closest-fit
   category (usually "Technical Issue" or "General Inquiry"), priority
   "Medium" (unknown severity -- don't assume High or Low), team
   "Tier 1 Support", and explain in reasoning that more detail is needed
   from the customer before final routing.
3. Ambiguous tickets that plausibly fit two categories: pick the single
   category tied to the primary blocking problem (the thing preventing the
   customer from resolving the rest themselves) and explicitly name the
   other plausible category you rejected and why, in the reasoning field.

Always return all five fields. reasoning must be one sentence and must be
specific to this message (no generic boilerplate).
"""
