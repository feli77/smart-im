-- Smart IM: bounded, nonblocking file mailbox; never starts a process or waits.
-- Rime owns Candidate objects and commits. Only an explicit Tab applies ranking.
local M = { processor = {}, filter = {} }
local sessions, serial = {}, 0
local MAX_CANDIDATES, MAX_INPUT, MAX_TEXT, MAX_CONTEXT = 9, 96, 64, 128
local REQUEST_TIMEOUT = 25
local PENDING = "AI计算中，请稍后再按Tab"
local UNAVAILABLE = "当前候选无法AI排序，保留原候选"
local UNNEEDED = "当前候选无需AI排序，保留原候选"

local function hex(value)
  return (value:gsub(".", function(byte) return string.format("%02x", string.byte(byte)) end))
end

local function bounded(value, limit)
  if type(value) ~= "string" or #value > limit * 4 then return false end
  local length = utf8.len(value)
  return length ~= nil and length <= limit
end

local function tail(value, limit)
  local length = utf8.len(value)
  if not length then return "" end
  local start = length > limit and utf8.offset(value, -limit) or 1
  return value:sub(start)
end

local function read_file(path, limit)
  local file = io.open(path, "rb")
  if not file then return nil end
  local data = file:read(limit + 1)
  file:close()
  return data and #data <= limit and data or nil
end

local function atomic_write(path, data)
  if #data > 16384 then return false end
  local temporary = path .. ".tmp"
  local file = io.open(temporary, "wb")
  if not file then return false end
  local written = file:write(data)
  local closed = file:close()
  if not written or not closed then os.remove(temporary); return false end
  if os.rename(temporary, path) then return true end
  -- Windows Lua rename cannot replace an existing file. Readers tolerate a gap.
  os.remove(path)
  if os.rename(temporary, path) then return true end
  os.remove(temporary)
  return false
end

local function online(state)
  local data = read_file(state.directory .. "/heartbeat", 32)
  if not data or not data:match("^%d+\n?$") then return false end
  local age = os.time() - tonumber(data)
  return age >= 0 and age <= 3
end

local function invalidate(state)
  state.revision = state.revision + 1
  state.snapshot, state.approved, state.sent, state.sent_at = nil, nil, nil, nil
  state.unavailable, state.retry = nil, nil
end

local function reset_history(state)
  state.history, state.last_commit = "", nil
  invalidate(state)
end

local function synchronize(state, context)
  local ai = context:get_option("smart_im_ai") and not context:get_option("ascii_mode")
  local learning = context:get_option("smart_im_learning")
  local app = context:get_property("client_app") or ""
  if state.ai ~= ai or state.learning ~= learning then
    invalidate(state)
    if not ai then state.history, state.last_commit = "", nil end
  end
  if state.app ~= app then reset_history(state) end
  if state.last_commit and os.time() - state.last_commit > 30 then reset_history(state) end
  state.ai, state.learning, state.app = ai, learning, app
  local snapshot = state.snapshot
  if snapshot and (snapshot.input ~= context.input or snapshot.caret ~= context.caret_pos
      or snapshot.context ~= state.history) then invalidate(state) end
end

local function prompt(context, message)
  if not context.composition:empty() then context.composition:back().prompt = message end
end

local function committed(state, context)
  synchronize(state, context)
  if not state.ai then return end
  local text = context:get_commit_text()
  if not text or text == "" or not bounded(text, MAX_TEXT) then
    reset_history(state)
    return
  end
  local pinyin = context.input or ""
  -- No select notifier: selecting a partial segment or pressing Tab never learns.
  if state.learning and bounded(pinyin, MAX_INPUT) and online(state) then
    state.sequence = state.sequence + 1
    local data = table.concat({
      "SMARTIM1\tCOMMIT\t" .. state.id .. "\t" .. state.sequence .. "\t1",
      hex(pinyin), hex(state.history), hex(text), ""
    }, "\n")
    atomic_write(state.directory .. "/" .. state.id .. "." .. state.sequence .. ".commit", data)
  end
  state.history = tail(state.history .. text, MAX_CONTEXT)
  state.last_commit = os.time()
  invalidate(state)
end

local function init(env)
  if env.smart_im_state then return end
  local context = env.engine.context
  local id = context:get_property("smart_im_session")
  local state = sessions[id]
  if not state then
    serial = serial + 1
    id = (os.time() .. "_" .. math.floor(os.clock() * 1000000) .. "_" .. serial .. "_"
      .. tostring({})):gsub("[^%w_-]", ""):sub(1, 64)
    state = {
      id = id, directory = rime_api.get_user_data_dir() .. "/smart_im_runtime",
      revision = 0, sequence = 0, history = "", references = 0, connections = {},
    }
    sessions[id] = state
    context:set_property("smart_im_session", id)
    synchronize(state, context)
    state.connections[#state.connections + 1] = context.commit_notifier:connect(function(ctx)
      committed(state, ctx)
    end)
    state.connections[#state.connections + 1] = context.option_update_notifier:connect(function(ctx, name)
      if name == "smart_im_ai" or name == "smart_im_learning" or name == "ascii_mode" then
        synchronize(state, ctx)
        ctx:refresh_non_confirmed_composition()
      end
    end)
    state.connections[#state.connections + 1] = context.unhandled_key_notifier:connect(function(_, key)
      if not key:release() then reset_history(state) end
    end)
  end
  state.references = state.references + 1
  env.smart_im_state = state
end

local function fini(env)
  local state = env.smart_im_state
  if not state then return end
  env.smart_im_state = nil
  state.references = state.references - 1
  if state.references > 0 then return end
  for _, connection in ipairs(state.connections) do connection:disconnect() end
  for _, suffix in ipairs({ ".request", ".request.tmp", ".response" }) do
    os.remove(state.directory .. "/" .. state.id .. suffix)
  end
  sessions[state.id] = nil
end

local function snapshot_of(state, context, candidates)
  if #candidates == 0 or not bounded(context.input, MAX_INPUT) then return nil, nil, UNAVAILABLE end
  local first = candidates[1]
  local start, finish = first.start, first._end
  if type(start) ~= "number" or type(finish) ~= "number" or start < 0
      or finish <= start or finish > #context.input then return nil, nil, UNAVAILABLE end
  local pinyin = context.input:sub(start + 1, finish)
  local parts = { hex(context.input), tostring(context.caret_pos), hex(state.history),
    state.learning and "1" or "0" }
  local eligible, slots = {}, {}
  for index, candidate in ipairs(candidates) do
    if type(candidate.start) ~= "number" or type(candidate._end) ~= "number"
        or candidate.start < 0 or candidate._end <= candidate.start
        or candidate._end > #context.input or candidate.text == ""
        or not bounded(candidate.text, MAX_TEXT) then return nil, nil, UNAVAILABLE end
    -- Rime can mix whole words and partial syllable matches in the same menu.
    -- Reorder only the first candidate's span, retaining every other slot.
    if candidate.start == start and candidate._end == finish then
      eligible[#eligible + 1], slots[#slots + 1] = candidate, index
    end
    -- Identity cannot survive a new Rime translation; all ranking-relevant
    -- content and metadata must nevertheless match the exact approved snapshot.
    parts[#parts + 1] = table.concat({ tostring(candidate.start), tostring(candidate._end), hex(candidate.text),
      hex(candidate.type or ""), hex(candidate.comment or ""), hex(candidate.preedit or ""),
      tostring(candidate.quality or 0) }, ":")
  end
  if #eligible < 2 then return nil, nil, UNNEEDED end
  return { fingerprint = table.concat(parts, "|"), input = context.input,
    caret = context.caret_pos, pinyin = pinyin, context = state.history,
    count = #eligible, slots = slots }, eligible
end

local function request(state, snapshot, candidates)
  if state.sent == state.revision then return end
  local lines = {
    "SMARTIM1\tRANK\t" .. state.id .. "\t" .. state.revision .. "\t" .. (state.learning and "1" or "0"),
    hex(snapshot.pinyin), hex(snapshot.context),
  }
  for _, candidate in ipairs(candidates) do lines[#lines + 1] = hex(candidate.text) end
  lines[#lines + 1] = ""
  if atomic_write(state.directory .. "/" .. state.id .. ".request", table.concat(lines, "\n")) then
    state.sent, state.sent_at, state.unavailable = state.revision, os.time(), nil
  else
    state.unavailable = "AI请求写入失败，保留原候选；请再按Tab重试"
  end
end

local function filter(input, env)
  local state, context = env.smart_im_state, env.engine.context
  synchronize(state, context)
  if not state.ai or not online(state) then
    if state.snapshot then invalidate(state) end
    for candidate in input:iter() do yield(candidate) end
    return
  end
  -- Keep the original objects and buffer only nine. The remainder stays lazy.
  local iterator, source, control = input:iter()
  local candidates = {}
  for _ = 1, MAX_CANDIDATES do
    local candidate = iterator(source, control)
    control = candidate
    if not candidate then break end
    candidates[#candidates + 1] = candidate
  end
  local snapshot, eligible, unavailable = snapshot_of(state, context, candidates)
  if not snapshot then
    if state.snapshot then invalidate(state) end
    state.unavailable = unavailable
  else
    if not state.snapshot or state.snapshot.fingerprint ~= snapshot.fingerprint then
      invalidate(state)
      state.snapshot = snapshot
    end
    request(state, snapshot, eligible)
  end
  local approved = state.approved
  if snapshot and approved and approved.fingerprint == snapshot.fingerprint then
    local replacements = {}
    for index, slot in ipairs(snapshot.slots) do
      replacements[slot] = eligible[approved.order[index]]
    end
    for index, candidate in ipairs(candidates) do yield(replacements[index] or candidate) end
  else
    for _, candidate in ipairs(candidates) do yield(candidate) end
  end
  for candidate in iterator, source, control do yield(candidate) end
end

local function ready_order(state)
  local data = read_file(state.directory .. "/" .. state.id .. ".response", 1024)
  if not data or not state.snapshot then return nil end
  local id, revision, status, indices = data:gsub("\r\n", "\n"):match(
    "^SMARTIM1\tRESULT\t([%w_-]+)\t(%d+)\t([a-z]+)\n([%d,]+)\n$")
  if id ~= state.id or tonumber(revision) ~= state.revision then return nil end
  if status == "fallback" then return nil, "fallback" end
  if status ~= "ok" then return nil end
  local order, used, canonical = {}, {}, {}
  for value in indices:gmatch("[^,]+") do
    local index = tonumber(value)
    if not index or index < 1 or index > state.snapshot.count or used[index] then return nil end
    used[index] = true
    order[#order + 1], canonical[#canonical + 1] = index, tostring(index)
  end
  if #order ~= state.snapshot.count or table.concat(canonical, ",") ~= indices then return nil end
  return order
end

local navigation = { [0xff1b] = true, [0xff50] = true, [0xff51] = true, [0xff52] = true,
  [0xff53] = true, [0xff54] = true, [0xff55] = true, [0xff56] = true, [0xff57] = true,
  [0xffff] = true }

local function request_prompt(state)
  if state.unavailable then return state.unavailable end
  if state.snapshot and state.sent == state.revision then return PENDING end
  return UNAVAILABLE
end

local function processor(key, env)
  local state, context = env.smart_im_state, env.engine.context
  synchronize(state, context)
  if key:release() then return 2 end
  local shortcut = key:ctrl() or key:alt() or key:super()
  if shortcut or navigation[key.keycode] or (key.keycode == 0xff08 and context.input == "") then
    reset_history(state)
    return 2
  end
  if key.keycode ~= 0xff09 or key:shift() or not state.ai or not context:has_menu() then return 2 end
  if not online(state) then
    invalidate(state)
    context:refresh_non_confirmed_composition()
    prompt(context, "AI服务未运行，保留原候选")
    return 1
  end
  local order, status = ready_order(state)
  if state.retry and not order then
    invalidate(state)
    context:refresh_non_confirmed_composition()
    prompt(context, request_prompt(state))
    return 1
  end
  if not state.snapshot or state.sent ~= state.revision then
    -- The service may have started after this menu was first displayed, or the
    -- context may just have expired. Rebuild to submit a fresh base snapshot.
    context:refresh_non_confirmed_composition()
    prompt(context, request_prompt(state))
    return 1
  end
  if not order then
    if status == "fallback" then
      state.retry = true
      prompt(context, "AI暂不可用，保留原候选；请再按Tab重试")
      return 1
    end
    -- The service expires idle mailbox files. An unchanged composition still
    -- needs a new revision after cleanup; keep a live in-flight request intact.
    if not read_file(state.directory .. "/" .. state.id .. ".request", 16384) then
      invalidate(state)
      context:refresh_non_confirmed_composition()
    elseif state.sent_at and os.time() - state.sent_at >= REQUEST_TIMEOUT then
      -- Keep this revision alive: queued local inference can still complete.
      -- Repeated Tab must not replace its request and starve a slow worker.
      prompt(context, "AI响应超时，保留原候选；可稍后再按Tab")
      return 1
    end
    prompt(context, request_prompt(state))
    return 1
  end
  state.approved = { fingerprint = state.snapshot.fingerprint, order = order }
  context:refresh_non_confirmed_composition()
  prompt(context, "Tab：AI排序")
  return 1
end

M.processor.init, M.processor.fini, M.processor.func = init, fini, processor
M.filter.init, M.filter.fini, M.filter.func = init, fini, filter
return M
