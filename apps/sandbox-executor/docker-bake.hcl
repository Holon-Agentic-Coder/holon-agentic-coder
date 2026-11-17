variable "CI" {
  default = "false"
}

# Overrides the beads (bd) pin baked into holon-base; empty keeps the Dockerfile's ARG BD_VERSION
# default as the single pin.
variable "BD_VERSION" {
  default = ""
}

target "common" {
  context = "apps/sandbox-executor"
  dockerfile = "Dockerfile"
  cache-from = CI == "true" ? ["type=gha,scope=holon-sandbox-executor"] : []
  cache-to = CI == "true" ? ["type=gha,mode=max,scope=holon-sandbox-executor"] : []
  args = BD_VERSION != "" ? { BD_VERSION = BD_VERSION } : {}
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

target "agent-gemini" {
  inherits = ["common"]
  target = "agent-gemini"
  tags = ["holon/agent-gemini"]
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
    "agent-gemini",
    "agent-opencode",
    "agent-pi",
    "agent-antigravity",
    "orchestrator"
  ]
}
