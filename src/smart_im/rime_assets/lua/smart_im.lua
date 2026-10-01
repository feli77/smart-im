-- Smart IM: bounded, nonblocking file mailbox; never starts a process or waits.
-- Rime owns Candidate objects and commits. Select is a service refresh signal.
local M = { processor = {}, filter = {} }
local sessions, serial = {}, 0
local MAX_CANDIDATES, MAX_INPUT, MAX_TEXT, MAX_CONTEXT = 9, 96, 64, 128
local REQUEST_TIMEOUT = 25
local PENDING = "AI计算中"
local UNAVAILABLE = "当前候选无法AI排序，保留原候选"
local UNNEEDED = "当前候选无需AI排序，保留原候选"
local NO_CONTEXT = "未获取到局部上下文，保留原候选"

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

local function local_context(value)
  -- One property publishes body text and its TSF focus/selection generation
  -- atomically. The frontend has already excluded the entire composition (or
  -- the selection it replaces); neither preedit nor committed history belongs here.
  if type(value) ~= "string" or #value > 2120 then return nil end
  local token, before, after = value:match("^1\t([a-zA-Z0-9_-]+)\t([a-fA-F0-9]*)\t([a-fA-F0-9]*)$")
  if not token or #token > 64 then return nil end
  local function decode(encoded)
    if #encoded % 2 ~= 0 or #encoded > MAX_CONTEXT * 8 then return nil end
    local decoded = encoded:gsub("..", function(byte) return string.char(tonumber(byte, 16)) end)
    if not bounded(decoded, MAX_CONTEXT) then return nil end
    return decoded
  end
  before, after = decode(before), decode(after)
  if not before or not after then return nil end
  return { token = token, before = before, after = after }
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
  -- Cancel queued work as soon as its composition is no longer applicable.
  -- An in-flight worker also checks this file before publishing its response.
  os.remove(state.directory .. "/" .. state.id .. ".request")
  os.remove(state.directory .. "/" .. state.id .. ".response")
  state.revision = state.revision + 1
  state.snapshot, state.approved, state.sent, state.sent_at = nil, nil, nil, nil
  state.unavailable = nil
end

local function selected_prefix(context, start)
  if start == 0 then return "" end
  local text, position = "", 0
  -- Selected segments remain inside the composition until the whole phrase is
  -- committed. Read only segments before the candidate range: looking up the
  -- active menu here would recursively invoke this filter.
  for _, segment in ipairs(context.composition:toSegmentation():get_segments()) do
    if segment.start >= start then break end
    if segment.start ~= position or segment._end <= position or segment._end > start
        or (segment.status ~= "kSelected" and segment.status ~= "kConfirmed") then return nil end
    local candidate = segment:get_selected_candidate()
    if not candidate or candidate.start ~= segment.start or candidate._end ~= segment._end
        or candidate.text == "" or not bounded(candidate.text, MAX_TEXT) then return nil end
    text, position = tail(text .. candidate.text, MAX_CONTEXT), segment._end
  end
  -- Never invent context across an unknown piece of composition.
  if position ~= start then return nil end
  return text
end

local function synchronize(state, context)
  local ai = context:get_option("smart_im_ai") and not context:get_option("ascii_mode")
  local learning = context:get_option("smart_im_learning")
  local app = context:get_property("client_app") or ""
  local property = context:get_property("smart_im_context") or ""
  if state.ai ~= ai or state.learning ~= learning then
    invalidate(state)
  end
  if state.app ~= app or state.context_property ~= property then invalidate(state) end
  state.ai, state.learning, state.app = ai, learning, app
  state.context_property, state.local_context = property, local_context(property)
  local snapshot = state.snapshot
  if snapshot and (snapshot.input ~= context.input or snapshot.caret ~= context.caret_pos
      or snapshot.selected_prefix ~= selected_prefix(context, snapshot.start)) then invalidate(state) end
end

local function prompt(context, message)
  if not context.composition:empty() then context.composition:back().prompt = message end
end

local function committed(state, context)
  synchronize(state, context)
  if not state.ai then return end
  local text = context:get_commit_text()
  if not text or text == "" or not bounded(text, MAX_TEXT) then
    invalidate(state)
    return
  end
  local pinyin = context.input or ""
  -- Only confirmed commits learn; refreshing and highlighting never learn.
  local preceding = state.snapshot and state.snapshot.before
    or (state.local_context and state.local_context.before)
  if state.learning and preceding and bounded(pinyin, MAX_INPUT) and online(state) then
    state.sequence = state.sequence + 1
    local data = table.concat({
      "SMARTIM1\tCOMMIT\t" .. state.id .. "\t" .. state.sequence .. "\t1",
      hex(pinyin), hex(preceding), hex(text), ""
    }, "\n")
    atomic_write(state.directory .. "/" .. state.id .. "." .. state.sequence .. ".commit", data)
  end
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
      revision = 0, sequence = 0, references = 0, connections = {},
    }
    sessions[id] = state
    context:set_property("smart_im_session", id)
    synchronize(state, context)
    state.connections[#state.connections + 1] = context.commit_notifier:connect(function(ctx)
      committed(state, ctx)
    end)
    state.connections[#state.connections + 1] = context.update_notifier:connect(function(ctx)
      -- Clear/focus loss also cancel requests, even if no more keys arrive.
      synchronize(state, ctx)
      if not ctx.composition:empty() then
        local segment = ctx.composition:back()
        if segment.prompt == "★ AI 推荐" and (segment.selected_index ~= 0 or not state.approved) then
          segment.prompt = ""
        end
      end
    end)
    state.connections[#state.connections + 1] = context.option_update_notifier:connect(function(ctx, name)
      if name == "smart_im_ai" or name == "smart_im_learning" or name == "ascii_mode" then
        synchronize(state, ctx)
        ctx:refresh_non_confirmed_composition()
      end
    end)
    state.connections[#state.connections + 1] = context.property_update_notifier:connect(function(ctx, name)
      if name ~= "smart_im_context" and name ~= "client_app" then return end
      local property, app = ctx:get_property("smart_im_context") or "", ctx:get_property("client_app") or ""
      if property == state.context_property and app == state.app then return end
      synchronize(state, ctx)
      prompt(ctx, "")
      if not state.restoring and not ctx.composition:empty() then
        -- Remove a displayed recommendation immediately on a TSF change,
        -- including focus loss with no subsequent key. A refresh must not
        -- recursively request/apply a result while restoring the native menu.
        state.restoring = true
        ctx:refresh_non_confirmed_composition()
        state.restoring = false
      end
    end)
    state.connections[#state.connections + 1] = context.unhandled_key_notifier:connect(function(_, key)
      if not key:release() and (key.keycode ~= 0xff09 or key:ctrl() or key:alt() or key:super()) then
        invalidate(state)
      end
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
  local body = state.local_context
  if not body then return nil, nil, NO_CONTEXT end
  if #candidates == 0 or not bounded(context.input, MAX_INPUT) then return nil, nil, UNAVAILABLE end
  local first = candidates[1]
  local start, finish = first.start, first._end
  if type(start) ~= "number" or type(finish) ~= "number" or start < 0
      or finish <= start or finish > #context.input then return nil, nil, UNAVAILABLE end
  local pinyin = context.input:sub(start + 1, finish)
  local selected = selected_prefix(context, start)
  if selected == nil then return nil, nil, UNAVAILABLE end
  local parts = { hex(context.input), tostring(context.caret_pos), body.token,
    hex(body.before), hex(body.after), hex(selected),
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
    -- content and metadata must nevertheless match the exact requested snapshot.
    parts[#parts + 1] = table.concat({ tostring(candidate.start), tostring(candidate._end), hex(candidate.text),
      hex(candidate.type or ""), hex(candidate.comment or ""), hex(candidate.preedit or ""),
      tostring(candidate.quality or 0) }, ":")
  end
  if #eligible < 2 then return nil, nil, UNNEEDED end
  return { fingerprint = table.concat(parts, "|"), input = context.input,
    caret = context.caret_pos, start = start, pinyin = pinyin, token = body.token,
    before = body.before, after = body.after, selected_prefix = selected,
    count = #eligible, slots = slots }, eligible
end

local function request(state, snapshot, candidates)
  if state.sent == state.revision then return end
  local lines = {
    "SMARTIM2\tRANK\t" .. state.id .. "\t" .. state.revision .. "\t" .. (state.learning and "1" or "0"),
    snapshot.token, hex(snapshot.pinyin), hex(snapshot.before), hex(snapshot.after), hex(snapshot.selected_prefix),
  }
  for _, candidate in ipairs(candidates) do lines[#lines + 1] = hex(candidate.text) end
  lines[#lines + 1] = ""
  if atomic_write(state.directory .. "/" .. state.id .. ".request", table.concat(lines, "\n")) then
    state.sent, state.sent_at, state.unavailable = state.revision, os.time(), nil
  else
    state.unavailable = "AI请求写入失败，保留原候选"
  end
end

local function ready_order(state)
  local data = read_file(state.directory .. "/" .. state.id .. ".response", 1024)
  if not data or not state.snapshot then return nil end
  local id, revision, status, token, indices = data:gsub("\r\n", "\n"):match(
    "^SMARTIM2\tRESULT\t([%w_-]+)\t(%d+)\t([a-z]+)\t([a-zA-Z0-9_-]+)\n([%d,]+)\n$")
  if id ~= state.id or tonumber(revision) ~= state.revision or token ~= state.snapshot.token then return nil end
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

local function request_prompt(state)
  if state.unavailable then return state.unavailable end
  if state.snapshot and state.sent == state.revision then
    if state.sent_at and os.time() - state.sent_at >= REQUEST_TIMEOUT then
      return "AI响应超时，保留原候选"
    end
    return PENDING
  end
  return UNAVAILABLE
end

local function result_prompt(state, order)
  if state.snapshot.before == "" and state.snapshot.after == ""
      and state.snapshot.selected_prefix == "" and not state.learning then
    local unchanged = true
    for index, value in ipairs(order) do
      if index ~= value then unchanged = false; break end
    end
    if unchanged then return "缺少上下文，保留原候选" end
  end
  return "★ AI 推荐"
end

local function filter(input, env)
  local state, context = env.smart_im_state, env.engine.context
  synchronize(state, context)
  if state.restoring then
    prompt(context, "")
    for candidate in input:iter() do yield(candidate) end
    return
  end
  if not state.ai or not online(state) then
    if state.snapshot then invalidate(state) end
    prompt(context, state.ai and "AI服务未运行，保留原候选" or "")
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
    if not state.snapshot or state.snapshot.fingerprint ~= snapshot.fingerprint
        or (state.sent == state.revision
          and not read_file(state.directory .. "/" .. state.id .. ".request", 16384)) then
      invalidate(state)
      state.snapshot = snapshot
    end
    request(state, snapshot, eligible)
    local order, status = ready_order(state)
    if order then
      state.approved = { fingerprint = snapshot.fingerprint, order = order }
    elseif status == "fallback" then
      state.unavailable = "AI暂不可用，保留原候选"
    end
  end
  local approved = state.approved
  if snapshot and approved and approved.fingerprint == snapshot.fingerprint then
    -- Rime highlights index zero when rebuilding this menu. Keep the original
    -- candidates (including genuine chains); put the recommendation in prompt.
    prompt(context, result_prompt(state, approved.order))
    local replacements = {}
    for index, slot in ipairs(snapshot.slots) do
      replacements[slot] = eligible[approved.order[index]]
    end
    for index, candidate in ipairs(candidates) do yield(replacements[index] or candidate) end
  else
    prompt(context, request_prompt(state))
    for _, candidate in ipairs(candidates) do yield(candidate) end
  end
  for candidate in iterator, source, control do yield(candidate) end
end

local navigation = { [0xff1b] = true, [0xff50] = true, [0xff51] = true, [0xff52] = true,
  [0xff53] = true, [0xff54] = true, [0xff55] = true, [0xff56] = true, [0xff57] = true,
  [0xffff] = true }

local function processor(key, env)
  local state, context = env.smart_im_state, env.engine.context
  synchronize(state, context)
  -- Weasel uses Select to wake TSF without inserting text. Consume both edges
  -- even after focus/input/options changed; never let the signal select a word.
  if key.keycode == 0xff60 then
    if key:release() or not state.ai or not context:has_menu() then return 1 end
    if not online(state) then
      local applied = state.approved ~= nil
      invalidate(state)
      if applied then context:refresh_non_confirmed_composition() end
      prompt(context, "AI服务未运行，保留原候选")
      return 1
    end
    local order, status = ready_order(state)
    if order and not state.approved then
      -- Select is also used by Weasel's own UI. Once the user moves away from
      -- the first candidate, leave that menu and highlight stable.
      if context.composition:back().selected_index ~= 0 then return 1 end
      -- The filter rechecks the complete base fingerprint after rebuilding.
      -- Its new segment starts at index zero, using Rime's native highlight.
      context:refresh_non_confirmed_composition()
    elseif status == "fallback" then
      state.unavailable = "AI暂不可用，保留原候选"
      prompt(context, state.unavailable)
    elseif not state.approved then
      prompt(context, request_prompt(state))
    end
    return 1
  end
  if key:release() then return 2 end
  local shortcut = key:ctrl() or key:alt() or key:super()
  if shortcut or navigation[key.keycode] or (key.keycode == 0xff08 and context.input == "") then
    invalidate(state)
    return 2
  end
  return 2
end

M.processor.init, M.processor.fini, M.processor.func = init, fini, processor
M.filter.init, M.filter.fini, M.filter.func = init, fini, filter
return M
