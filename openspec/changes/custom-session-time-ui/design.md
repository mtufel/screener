# Design: Custom Time Range Input in Web Dashboard UI

## Architecture & UI Components

### 1. Control Bar HTML Layout
For both `FVG Session` and `Entry Session`:
```html
<div>
  <label class="block text-[11px] font-semibold text-slate-400 mb-1">FVG Session</label>
  <div class="space-y-1">
    <select id="extremeSessions" onchange="handleExtremeSessionSelect('sessions', this.value)" class="...">
      <option value="ALL">24/7 (ALL)</option>
      <option value="NY">NY (13-22 UTC)</option>
      <option value="LONDON">London (07-16 UTC)</option>
      <option value="LONDON,NY">London + NY</option>
      <option value="ASIA">Asia (00-08 UTC)</option>
      <option value="LONDON_KZ">London KZ (07-10 UTC)</option>
      <option value="NY_KZ">NY KZ (12-15 UTC)</option>
      <option value="CUSTOM">Custom (UTC)...</option>
    </select>
    <input
      type="text"
      id="extremeSessionsCustom"
      placeholder="e.g. 13:30-20:00 (UTC)"
      onchange="saveExtremeConfig('sessions', this.value.trim())"
      class="hidden px-2.5 py-1 rounded-lg bg-dark-950 border border-amber-500/40 text-[11px] font-mono text-amber-300 w-full"
    />
  </div>
</div>
```

### 2. Backtest HTML Layout
Similarly, for `extremeBtSession` and `extremeBtEntrySession`:
```html
<select id="extremeBtSession" onchange="handleBtSessionSelect('extremeBtSession', 'extremeBtSessionCustom', this.value)" ...>
  ...
  <option value="CUSTOM">Custom (UTC)...</option>
</select>
<input
  type="text"
  id="extremeBtSessionCustom"
  placeholder="e.g. 13:30-20:00 (UTC)"
  class="hidden px-2.5 py-1 rounded-lg bg-dark-950 border border-amber-500/40 text-[11px] font-mono text-amber-300 w-full"
/>
```

### 3. JavaScript Handlers
- `handleExtremeSessionSelect(param, value)`: If `value === 'CUSTOM'`, shows custom input and focuses it. Otherwise hides input and calls `saveExtremeConfig(param, value)`.
- `syncExtremeConfigUI(data)`:
  - If `data.sessions` is one of standard presets, selects it and hides custom input.
  - If `data.sessions` is custom string, selects `CUSTOM`, unhides input, and sets `input.value = data.sessions`.
- `runExtremeBacktest()`:
  - Evaluates `extremeBtSession.value === 'CUSTOM' ? extremeBtSessionCustom.value : extremeBtSession.value`.
  - Evaluates `extremeBtEntrySession.value === 'CUSTOM' ? extremeBtEntrySessionCustom.value : extremeBtEntrySession.value`.
