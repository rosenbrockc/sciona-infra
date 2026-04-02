package bounty

import data.cancellable_statuses
import data.fee_schedule
import data.identity_tiers
import data.tier_limits
import rego.v1

default allow_create := false
allow_create if {
    not input.user.is_blacklisted
    identity_tiers[input.user.identity_tier].can_create_bounty
}

allow_create if {
    not input.user.is_blacklisted
    identity_tiers[input.user.identity_tier].can_fund
}

deny_create contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny_create contains msg if {
    not identity_tiers[input.user.identity_tier].can_create_bounty
    msg := sprintf("identity tier '%s' cannot create bounties", [input.user.identity_tier])
}

default allow_fund := false
allow_fund if {
    not input.user.is_blacklisted
    input.user.user_id == input.bounty.principal_id
    input.bounty.status == "draft"
    identity_tiers[input.user.identity_tier].can_fund
}

deny_fund contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can fund it"
}

deny_fund contains msg if {
    input.bounty.status != "draft"
    msg := sprintf("cannot fund bounty in '%s' state", [input.bounty.status])
}

deny_fund contains msg if {
    not identity_tiers[input.user.identity_tier].can_fund
    msg := sprintf("identity tier '%s' cannot fund bounties", [input.user.identity_tier])
}

default allow_submit := false
allow_submit if {
    not input.user.is_blacklisted
    input.user.user_id != input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
}

deny_submit contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny_submit contains msg if {
    input.user.user_id == input.bounty.principal_id
    msg := "bounty creator cannot submit to own bounty"
}

deny_submit contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("cannot submit to bounty in '%s' state", [input.bounty.status])
}

default allow_cancel := false
allow_cancel if {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status in cancellable_statuses
}

deny_cancel contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can cancel it"
}

deny_cancel contains msg if {
    not input.bounty.status in cancellable_statuses
    msg := sprintf("cannot cancel bounty in '%s' state", [input.bounty.status])
}

default allow_update_target := false
allow_update_target if {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
}

deny_update_target contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can update the target"
}

deny_update_target contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("can only update target for open/submitted bounties, got '%s'", [input.bounty.status])
}

default valid_escrow := false
valid_escrow if {
    limit := tier_limits[input.bounty.tier]
    input.bounty.escrow_amount <= limit.max_escrow
}
