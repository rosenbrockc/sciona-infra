package payout

import data.fee_schedule
import rego.v1

default valid_plan := false
valid_plan if {
    total := sum([recipient.amount | some recipient in input.plan.recipients])
    total == input.plan.escrow_amount
}

default valid_split_percentages := false
valid_split_percentages if {
    platform_total := sum([recipient.amount | some recipient in input.plan.recipients; recipient.role == "platform"])
    architect_total := sum([recipient.amount | some recipient in input.plan.recipients; recipient.role == "architect"])
    originator_total := sum([recipient.amount | some recipient in input.plan.recipients; recipient.role == "originator"])
    escrow := input.plan.escrow_amount

    abs(platform_total - escrow * fee_schedule.platform_pct / 100) < 0.02
    abs(architect_total - escrow * fee_schedule.architect_pct / 100) < 0.02
    abs(originator_total - escrow * fee_schedule.originator_pct / 100) < 0.02
}

default valid_cancellation_fee := false
valid_cancellation_fee if {
    not input.has_submissions
    expected := input.escrow_amount * fee_schedule.cancellation_no_submissions_pct / 100
    input.cancellation_fee == expected
}

valid_cancellation_fee if {
    input.has_submissions
    expected := input.escrow_amount * fee_schedule.cancellation_with_submissions_pct / 100
    input.cancellation_fee == expected
}

default all_recipients_have_accounts := false
all_recipients_have_accounts if {
    missing := [
        recipient |
        some recipient in input.plan.recipients
        recipient.role != "platform"
        recipient.amount > 0
        recipient.stripe_account_id == ""
    ]
    count(missing) == 0
}

deny contains msg if {
    not valid_plan
    total := sum([recipient.amount | some recipient in input.plan.recipients])
    msg := sprintf("payout total %v != escrow %v (conservation violated)", [total, input.plan.escrow_amount])
}
