variable "CI" {
  default = "false"
}

target "common" {
  context = "apps/sandbox-executor"
  dockerfile = "Dockerfile"
  cache-from = CI == "true" ? ["type=gha,scope=holon-sandbox-executor"] : []
  cache-to = CI == "true" ? ["type=gha,mode=max,scope=holon-sandbox-executor"] : []
}

target "base" {
  inherits = ["common"]
  target = "holon-base"
  tags = ["holon/base"]
}

target "agent-claude" {
  inherits = ["common"]
  target = "agent-claude"
  tags = ["holon/agent-claude"]
}

target "agent-codex" {
  inherits = ["common"]
  target = "agent-codex"
  tags = ["holon/agent-codex"]
}

target "agent-opencode" {
  inherits = ["common"]
  target = "agent-opencode"
  tags = ["holon/agent-opencode"]
}

target "agent-pi" {
  inherits = ["common"]
  target = "agent-pi"
  tags = ["holon/agent-pi"]
}

target "agent-antigravity" {
  inherits = ["common"]
  target = "agent-antigravity"
  tags = ["holon/agent-antigravity"]
}

target "orchestrator" {
  inherits = ["common"]
  target = "holon-orchestrator"
  tags = ["holon/orchestrator"]
}

group "default" {
  targets = [
    "base",
    "agent-claude",
    "agent-codex",
    "agent-opencode",
    "agent-pi",
    "agent-antigravity",
    "orchestrator"
  ]
}
