#!/usr/bin/env bats
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

@test "deployment helper validates immutable inputs without Docker" {
  run bash "$BATS_TEST_DIRNAME/../deploy-unraid.sh" --self-test
  [ "$status" -eq 0 ]
  [[ "$output" == *"self-test passed"* ]]
}
