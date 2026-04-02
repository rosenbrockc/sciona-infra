package submission

import rego.v1

default allow := false
allow if {
    not input.user.is_blacklisted
    input.user.user_id != input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
    valid_receipt
}

default valid_receipt := false
valid_receipt if {
    count(input.submission.receipt_json) > 0
}

valid_receipt if {
    input.submission.receipt_s3 != ""
}

deny contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny contains msg if {
    input.user.user_id == input.bounty.principal_id
    msg := "bounty creator cannot submit to own bounty"
}

deny contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("bounty is in '%s' state, not accepting submissions", [input.bounty.status])
}

deny contains msg if {
    not valid_receipt
    msg := "submission must include a receipt (receipt_json or receipt_s3)"
}
