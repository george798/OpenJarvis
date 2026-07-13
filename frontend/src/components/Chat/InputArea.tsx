import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Square, Paperclip, Search, Mic, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore, generateId, type VoiceOrbState } from '../../lib/store';
import { streamChat, streamResearch } from '../../lib/sse';
import { fetchSavings } from '../../lib/api';
import { listConnectors, getSyncStatus } from '../../lib/connectors-api';
import { MicButton } from './MicButton';
import { useSpeech } from '../../hooks/useSpeech';
import { useTTS } from '../../hooks/useTTS';
import type {
  ChatMessage,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  TokenUsage,
  ToolCallInfo,
} from '../../types';

// While Deep Research is toggled on, poll connected sources for sync
// progress so we can surface "Searching over N items — sync in progress"
// next to the toggle. Polling is gated on `enabled` so toggling DR off
// stops the network chatter immediately.
function useResearchCorpusSync(enabled: boolean): {
  syncing: boolean;
  itemsSynced: number;
} {
  const [state, setState] = useState({ syncing: false, itemsSynced: 0 });

  useEffect(() => {
    if (!enabled) {
      setState({ syncing: false, itemsSynced: 0 });
      return;
    }
    let cancelled = false;

    const poll = async () => {
      try {
        const list = await listConnectors();
        const connected = list.filter((c) => c.connected);
        if (connected.length === 0) {
          if (!cancelled) setState({ syncing: false, itemsSynced: 0 });
          return;
        }
        const results = await Promise.all(
          connected.map(async (c) => {
            try {
              return await getSyncStatus(c.connector_id);
            } catch {
              return null;
            }
          }),
        );
        let syncing = false;
        let itemsSynced = 0;
        for (const r of results) {
          if (!r) continue;
          if (r.state === 'syncing') syncing = true;
          itemsSynced += r.items_synced ?? 0;
        }
        if (!cancelled) setState({ syncing, itemsSynced });
      } catch {
        // Network blip — leave previous state intact.
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [enabled]);

  return state;
}

export function InputArea() {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const models = useAppStore((s) => s.models);
  const setSelectedModel = useAppStore((s) => s.setSelectedModel);
  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const voiceAssistantEnabled = useAppStore((s) => s.settings.voiceAssistantEnabled);
  const ttsEnabled = useAppStore((s) => s.settings.ttsEnabled);
  const ttsVoiceId = useAppStore((s) => s.settings.ttsVoiceId);
  const maxTokens = useAppStore((s) => s.settings.maxTokens);
  const temperature = useAppStore((s) => s.settings.temperature);
  const createConversation = useAppStore((s) => s.createConversation);
  const addMessage = useAppStore((s) => s.addMessage);
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant);
  const setStreamState = useAppStore((s) => s.setStreamState);
  const resetStream = useAppStore((s) => s.resetStream);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const deepResearchModel = useAppStore((s) => s.settings.deepResearchModel);
  const setDeepResearch = useAppStore((s) => s.setDeepResearch);
  const activeSkill = useAppStore((s) => s.activeSkill);
  const setSkillsPickerOpen = useAppStore((s) => s.setSkillsPickerOpen);
  const setActiveSkill = useAppStore((s) => s.setActiveSkill);
  const corpusSync = useResearchCorpusSync(deepResearch);

  const { state: speechState, error: speechError, available: speechAvailable, startRecording, stopRecording } = useSpeech();
  const { speak, cancel: cancelTTS, available: ttsAvailable, speaking: ttsSpeaking, serverTts } = useTTS(ttsVoiceId || undefined);

  const voiceInputActive = speechEnabled || voiceAssistantEnabled;
  const updateSettings = useAppStore((s) => s.updateSettings);

  // Re-sync voice settings after docker bootstrap writes localStorage.
  useEffect(() => {
    try {
      const raw = localStorage.getItem('openjarvis-settings');
      if (!raw) return;
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      updateSettings({
        apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : undefined,
        apiUrl: typeof parsed.apiUrl === 'string' ? parsed.apiUrl : undefined,
        speechEnabled: parsed.speechEnabled !== false,
        voiceAssistantEnabled: parsed.voiceAssistantEnabled !== false,
        ttsEnabled: parsed.ttsEnabled !== false,
      });
    } catch {
      // ignore malformed settings
    }
  }, [updateSettings]);

  useEffect(() => {
    if (!speechError) return;
    if (speechError.startsWith('Keep speaking')) {
      toast.message(speechError);
    } else {
      toast.error(speechError);
    }
  }, [speechError]);

  // Abort in-flight stream when the user switches models mid-generation.
  // This prevents errors from trying to continue a stream with a stale model.
  const prevModelRef = useRef(selectedModel);
  useEffect(() => {
    if (prevModelRef.current !== selectedModel && streamState.isStreaming) {
      abortRef.current?.abort();
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      abortRef.current = null;
    }
    prevModelRef.current = selectedModel;
  }, [selectedModel, streamState.isStreaming, resetStream]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [input]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    resetStream();
  }, [resetStream]);

  const sendMessage = useCallback(async (contentOverride?: string, modelOverride?: string) => {
    const content = (contentOverride ?? input).trim();
    if (!content || streamState.isStreaming) return;
    const model =
      modelOverride ??
      useAppStore.getState().selectedModel ??
      selectedModel;

    // Hermes-style slash commands (web chat)
    if (content.startsWith('/')) {
      const parts = content.split(/\s+/);
      const cmd = parts[0].toLowerCase();
      if (cmd === '/clear') {
        setInput('');
        if (activeId) {
          useAppStore.setState({ messages: { ...useAppStore.getState().messages, [activeId]: [] } });
        }
        toast.message('Conversation cleared');
        return;
      }
      if (cmd === '/voice' && parts[1]) {
        const on = ['on', 'true', '1', 'yes'].includes(parts[1].toLowerCase());
        updateSettings({ ttsEnabled: on, voiceAssistantEnabled: on, speechEnabled: on });
        toast.message(`Voice ${on ? 'enabled' : 'disabled'}`);
        setInput('');
        return;
      }
      if (cmd === '/model' && parts[1]) {
        useAppStore.getState().setSelectedModel(parts[1]);
        toast.message(`Model set to ${parts[1]}`);
        setInput('');
        return;
      }
      if (cmd === '/skills') {
        setInput('');
        setSkillsPickerOpen(true);
        return;
      }
      if (cmd === '/help') {
        setInput('');
        toast.message('/model, /voice, /skills, /clear — Hermes-style chat commands');
        return;
      }
    }

    if (!model) {
      toast.error('Pick a model first (⌘K)');
      return;
    }

    if (modelOverride && model !== useAppStore.getState().selectedModel) {
      setSelectedModel(model);
    }

    setInput('');

    let convId = activeId;
    if (!convId) {
      convId = createConversation(model);
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    addMessage(convId, userMsg);

    // Build API messages before adding assistant placeholder
    const currentMessages = useAppStore.getState().messages;
    const apiMessages = currentMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isResearch: deepResearch || undefined,
    };
    addMessage(convId, assistantMsg);

    // Start streaming
    const startTime = Date.now();
    const timer = setInterval(() => {
      setStreamState({ elapsedMs: Date.now() - startTime });
    }, 100);
    timerRef.current = timer;

    const controller = new AbortController();
    abortRef.current = controller;

    let accumulatedContent = '';
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    const toolCalls: ToolCallInfo[] = [];
    const researchTraces: ResearchSearchTrace[] = [];
    const researchSourcesByRef = new Map<number, ResearchSource>();
    const flushSources = () =>
      Array.from(researchSourcesByRef.values()).sort((a, b) => a.ref - b.ref);
    let lastFlush = 0;
    let ttftMs: number | undefined;

    setStreamState({
      isStreaming: true,
      phase: deepResearch ? 'Researching...' : 'Generating...',
      elapsedMs: 0,
      activeToolCalls: [],
      content: '',
    });
    useAppStore.getState().addLogEntry({
      timestamp: Date.now(),
      level: 'info',
      category: 'chat',
      message: deepResearch
        ? `Research: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}"${deepResearchModel ? ` → ${deepResearchModel}` : ''}`
        : `Request: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}" → ${model}`,
    });

    try {
      if (deepResearch) {
        for await (const ev of streamResearch(
          content,
          deepResearchModel || undefined,
          controller.signal,
        )) {
          if (ev.type === 'search_call') {
            const trace: ResearchSearchTrace = {
              id: generateId(),
              query: ev.arguments?.query ?? '',
              person: ev.arguments?.person,
              timeRange: ev.arguments?.time_range,
              status: 'pending',
            };
            researchTraces.push(trace);
            setStreamState({ phase: `Searching: ${trace.query}` });
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'info',
              category: 'tool',
              message: `Search: "${trace.query}"${trace.person ? ` (person: ${trace.person})` : ''}`,
            });
          } else if (ev.type === 'search_result') {
            const pending = [...researchTraces].reverse().find((t) => t.status === 'pending');
            if (pending) {
              pending.status = 'complete';
              pending.numHits = ev.num_hits;
              pending.topTitles = ev.top_titles;
            }
            if (ev.sources) {
              for (const src of ev.sources) {
                if (src && typeof src.ref === 'number' && !researchSourcesByRef.has(src.ref)) {
                  researchSourcesByRef.set(src.ref, src);
                }
              }
            }
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
          } else if (ev.type === 'synthesis') {
            if (!ttftMs) ttftMs = Date.now() - startTime;
            accumulatedContent += ev.text;
            setStreamState({ content: accumulatedContent, phase: '' });
            const now = Date.now();
            if (now - lastFlush >= 80) {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                undefined,
                [...researchTraces],
                flushSources(),
              );
              lastFlush = now;
            }
          } else if (ev.type === 'system_metrics') {
            // Live GPU sample — feed straight to the System panel so Power
            // (W) and Energy (kJ) tick up in real time as the agent runs.
            useAppStore.getState().setLiveEnergy({
              power_w: ev.power_w,
              energy_j: ev.energy_j,
              duration_s: ev.duration_s,
            });
          } else if (ev.type === 'error') {
            // Backend setup/worker failure (Ollama down, planner model
            // missing, KnowledgeStore locked, etc.). Without surfacing the
            // message, the user sees only the generic "No response was
            // generated" fallback and has no way to self-diagnose.
            const msg = ev.message || 'Research failed (no detail provided)';
            accumulatedContent = accumulatedContent
              ? `${accumulatedContent}\n\n**Research stopped:** ${msg}`
              : `**Research failed:** ${msg}`;
            setStreamState({ content: accumulatedContent, phase: '' });
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'error',
              category: 'chat',
              message: `Deep Research error: ${msg}`,
            });
            toast.error(msg, { duration: 8000 });
          } else if (ev.type === 'done') {
            if (ev.usage) {
              usage = {
                prompt_tokens: ev.usage.prompt_tokens ?? 0,
                completion_tokens: ev.usage.completion_tokens ?? 0,
                total_tokens:
                  ev.usage.total_tokens ??
                  (ev.usage.prompt_tokens ?? 0) +
                    (ev.usage.completion_tokens ?? 0),
              };
              // Optimistically roll this research turn into the session
              // counters so the Session panel updates the moment the
              // stream finishes, regardless of how /v1/savings aggregates
              // research telemetry server-side.
              useAppStore.getState().incrementSavings(usage);
            }
            // Hold the final live numbers visible for a beat so the panel
            // doesn't flash to 0 between the SSE close and the next
            // /v1/telemetry/energy poll picking up the persisted record.
            window.setTimeout(() => {
              useAppStore.getState().setLiveEnergy(null);
            }, 1500);
            break;
          }
        }
      } else {
      for await (const sseEvent of streamChat(
        {
          model,
          messages: apiMessages,
          stream: true,
          temperature,
          max_tokens: maxTokens,
          skill: activeSkill || undefined,
        },
        controller.signal,
      )) {
        const eventName = sseEvent.event;

        if (eventName === 'agent_turn_start') {
          setStreamState({ phase: 'Agent thinking...' });
        } else if (eventName === 'inference_start') {
          setStreamState({ phase: 'Generating...' });
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'chat',
            message: `Generating with ${model}...`,
          });
        } else if (eventName === 'tool_call_start') {
          try {
            const data = JSON.parse(sseEvent.data);
            // The backend may send `arguments` as an object (e.g. image_generate
            // sends {prompt, size}); ToolCallInfo.arguments must be a string or
            // React crashes rendering it (error #31).
            const argsStr =
              typeof data.arguments === 'string'
                ? data.arguments
                : data.arguments != null
                  ? JSON.stringify(data.arguments)
                  : '';
            const tc: ToolCallInfo = {
              id: generateId(),
              tool: data.tool,
              arguments: argsStr,
              status: 'running',
            };
            toolCalls.push(tc);
            setStreamState({
              phase: `Calling ${data.tool}...`,
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(), level: 'info', category: 'tool',
              message: `Calling ${data.tool}(${argsStr})`,
            });
          } catch {}
        } else if (eventName === 'tool_call_end') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc = toolCalls.find(
              (t) => t.tool === data.tool && t.status === 'running',
            );
            if (tc) {
              tc.status = data.success ? 'success' : 'error';
              tc.latency = data.latency;
              tc.result =
                typeof data.result === 'string'
                  ? data.result
                  : data.result != null
                    ? JSON.stringify(data.result)
                    : undefined;
            }
            setStreamState({
              phase: 'Generating...',
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
          } catch {}
        } else {
          try {
            const data = JSON.parse(sseEvent.data);
            const delta = data.choices?.[0]?.delta;
            if (data.usage) usage = data.usage;
            if (data.complexity) complexity = data.complexity;
            if (delta?.content) {
              if (!ttftMs) ttftMs = Date.now() - startTime;
              accumulatedContent += delta.content;
              setStreamState({ content: accumulatedContent, phase: '' });

              const now = Date.now();
              if (now - lastFlush >= 80) {
                updateLastAssistant(
                  convId,
                  accumulatedContent,
                  toolCalls.length > 0 ? [...toolCalls] : undefined,
                );
                lastFlush = now;
              }
            }
            if (data.choices?.[0]?.finish_reason === 'stop') break;
          } catch {}
        }
      }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // User cancelled or model switch — keep whatever was accumulated
        if (!accumulatedContent) {
          if (toolCalls.length > 0) {
            const last = toolCalls[toolCalls.length - 1];
            accumulatedContent =
              `(Generation stopped after ${toolCalls.length} tool call(s). ` +
              `Last: ${last.tool} (${last.status}). ` +
              `Sync/index jobs may still be running — check Data Sources or ask ` +
              `Jarvis to run connector_manage sync_status.)`;
          } else {
            accumulatedContent = '(Generation stopped)';
          }
        }
      } else {
        const errMsg = err?.message || String(err);
        accumulatedContent =
          accumulatedContent || `Error: ${errMsg}`;
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(), level: 'error', category: 'chat',
          message: `Stream error: ${errMsg}`,
        });
      }
      // If we tore out mid-research, make sure the live System panel
      // numbers don't get stuck on the last sample.
      useAppStore.getState().setLiveEnergy(null);
    } finally {
      if (!accumulatedContent) {
        // The backend now emits a diagnostic delta when a turn yields no text,
        // so this should rarely fire. If it does and tools ran, surface the
        // last tool result instead of the opaque generic message.
        if (toolCalls.length > 0) {
          const last = toolCalls[toolCalls.length - 1];
          const res = last.result ? ` — ${String(last.result).slice(0, 400)}` : '';
          accumulatedContent =
            `Ran ${toolCalls.length} tool(s) but no final text was generated. ` +
            `Last: ${last.tool} (${last.status})${res}`;
        } else {
          accumulatedContent =
            'No response was generated — the model returned no text. This often ' +
            'means the context window overflowed on a long chat. Try a shorter ' +
            'message or start a new chat.';
        }
      }
      const totalMs = Date.now() - startTime;
      const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/', 'MiniMax-', 'chatgpt-'];
      const engineLabel = _CLOUD_PREFIXES.some(p => model.startsWith(p)) ? 'cloud' : 'ollama';
      const telemetry: MessageTelemetry = {
        engine: engineLabel,
        model_id: model,
        total_ms: totalMs,
        ttft_ms: ttftMs,
        tokens_per_sec: usage?.completion_tokens
          ? usage.completion_tokens / (totalMs / 1000)
          : undefined,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      // Digest audio is only for explicit digest responses — never attach the
      // cached morning-digest clip to ordinary chat replies (was causing confusion).
      let audioMeta: { url: string } | undefined;

      updateLastAssistant(
        convId,
        accumulatedContent,
        toolCalls.length > 0 ? toolCalls : undefined,
        usage,
        telemetry,
        audioMeta,
        researchTraces.length > 0 ? researchTraces : undefined,
        researchSourcesByRef.size > 0 ? flushSources() : undefined,
      );
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'chat',
        message: `Response: ${accumulatedContent.length} chars`,
      });
      abortRef.current = null;

      if (
        voiceAssistantEnabled &&
        ttsEnabled &&
        ttsAvailable &&
        accumulatedContent &&
        !accumulatedContent.startsWith('Error:') &&
        !toolCalls.some((tc) => tc.tool === 'text_to_speech' && tc.status === 'success')
      ) {
        speak(accumulatedContent);
      }

      // Research path updates session counters optimistically from the
      // `done` event's usage payload — re-fetching here would overwrite
      // it with a potentially stale snapshot if the server's research
      // telemetry hasn't been merged into /v1/savings yet.
      if (!deepResearch) {
        fetchSavings()
          .then((data) => useAppStore.getState().setSavings(data))
          .catch(() => {});
      }
    }
  }, [
    input,
    activeId,
    selectedModel,
    streamState.isStreaming,
    setSelectedModel,
    createConversation,
    addMessage,
    updateLastAssistant,
    setStreamState,
    resetStream,
    deepResearch,
    deepResearchModel,
    temperature,
    maxTokens,
    activeSkill,
    voiceAssistantEnabled,
    ttsEnabled,
    ttsAvailable,
    speak,
  ]);

  const handleMicClick = useCallback(async () => {
    if (speechState === 'transcribing') {
      toast.message('Still transcribing…');
      return;
    }
    if (streamState.isStreaming) {
      toast.message('Wait for the current response to finish');
      return;
    }

    if (!voiceInputActive) {
      updateSettings({ speechEnabled: true, voiceAssistantEnabled: true });
      toast.message('Voice assistant enabled');
    }

    if (speechState === 'recording') {
      try {
        const text = await stopRecording();
        if (!text) return;
        if (voiceAssistantEnabled) {
          let modelForVoice = selectedModel;
          if (!modelForVoice && models.length > 0) {
            modelForVoice = models[0].id;
            setSelectedModel(modelForVoice);
          }
          if (!modelForVoice) {
            setInput((prev) => (prev ? `${prev} ${text}` : text));
            toast.message('Transcribed — pick a model (Ctrl+K) and press Enter to send');
            return;
          }
          await sendMessage(text, modelForVoice);
        } else {
          setInput((prev) => (prev ? prev + ' ' + text : text));
        }
      } catch {
        // Error is captured in useSpeech
      }
    } else {
      cancelTTS();
      await startRecording();
    }
  }, [
    speechState,
    streamState.isStreaming,
    voiceInputActive,
    selectedModel,
    startRecording,
    stopRecording,
    voiceAssistantEnabled,
    sendMessage,
    cancelTTS,
    updateSettings,
    models,
    setSelectedModel,
  ]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'm' && !e.shiftKey) {
        e.preventDefault();
        void handleMicClick();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handleMicClick]);

  const voiceStatus =
    speechState === 'recording'
      ? 'Listening…'
      : speechState === 'transcribing'
        ? 'Transcribing…'
        : ttsSpeaking
          ? 'Speaking…'
          : streamState.isStreaming && voiceAssistantEnabled
            ? 'Thinking…'
            : null;

  const orbState: VoiceOrbState =
    speechState === 'recording'
      ? 'recording'
      : speechState === 'transcribing'
        ? 'transcribing'
        : ttsSpeaking
          ? 'speaking'
          : streamState.isStreaming && voiceAssistantEnabled
            ? 'thinking'
            : 'idle';

  const setVoiceControl = useAppStore((s) => s.setVoiceControl);
  useEffect(() => {
    setVoiceControl({
      toggleMic: () => {
        void handleMicClick();
      },
      orbState,
      voiceStatus,
      disabled: speechState === 'transcribing' || (streamState.isStreaming && speechState !== 'recording'),
    });
    return () => setVoiceControl(null);
  }, [handleMicClick, orbState, voiceStatus, speechState, streamState.isStreaming, voiceAssistantEnabled, setVoiceControl]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="px-4 pb-4 pt-2" style={{ maxWidth: 'var(--chat-max-width)', margin: '0 auto', width: '100%' }}>
      <div className="mb-2 flex flex-col gap-1">
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => setDeepResearch(!deepResearch)}
            disabled={streamState.isStreaming}
            aria-pressed={deepResearch}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50"
            style={{
              background: deepResearch ? 'var(--color-accent-subtle)' : 'transparent',
              border: `1px solid ${deepResearch ? 'var(--color-accent)' : 'var(--color-border)'}`,
              color: deepResearch ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
            title={deepResearch ? 'Deep Research: on' : 'Deep Research: off'}
          >
            <Search size={12} />
            Deep Research
          </button>
          {activeSkill && (
            <button
              type="button"
              onClick={() => setSkillsPickerOpen(true)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer"
              style={{
                background: 'var(--color-accent-subtle)',
                border: '1px solid var(--color-accent)',
                color: 'var(--color-accent)',
              }}
              title={`Active skill: ${activeSkill} — click to change`}
            >
              <Sparkles size={12} />
              {activeSkill}
              <span
                role="button"
                tabIndex={0}
                className="ml-0.5 opacity-70 hover:opacity-100"
                title="Clear skill"
                onClick={(e) => {
                  e.stopPropagation();
                  setActiveSkill(null);
                  toast.message('Skill cleared');
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.stopPropagation();
                    setActiveSkill(null);
                  }
                }}
              >
                ×
              </span>
            </button>
          )}
          <button
            type="button"
            onClick={() => setSkillsPickerOpen(true)}
            disabled={streamState.isStreaming}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50"
            style={{
              background: 'transparent',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-tertiary)',
            }}
            title="Choose a skill (/skills)"
          >
            <Sparkles size={12} />
            Skills
          </button>
          {voiceAssistantEnabled && (
            <span
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs"
              style={{
                background: voiceStatus ? 'var(--color-accent-subtle)' : 'transparent',
                border: `1px solid ${voiceStatus ? 'var(--color-accent)' : 'var(--color-border)'}`,
                color: voiceStatus ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
              }}
              title={serverTts ? 'Server TTS (Jarvis voice)' : 'Browser TTS fallback'}
            >
              <Mic size={12} />
              Voice {voiceStatus ? `· ${voiceStatus}` : '· ready'}
            </span>
          )}
        </div>
        {deepResearch && corpusSync.syncing && corpusSync.itemsSynced > 0 && (
          <div
            className="text-[11px] leading-snug"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            Searching over{' '}
            <span key={corpusSync.itemsSynced} className="sync-bump" style={{ color: 'var(--color-text-secondary)' }}>
              {corpusSync.itemsSynced.toLocaleString()}
            </span>{' '}
            items — sync in progress, results will improve as more data is indexed.
          </div>
        )}
      </div>
      <div
        className="flex items-center gap-2 rounded-2xl px-4 py-3 transition-shadow"
        style={{
          background: 'var(--color-input-bg)',
          border: '1px solid var(--color-input-border)',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={selectedModel
            ? (voiceAssistantEnabled ? 'Message OpenJarvis or tap mic to talk...' : 'Message OpenJarvis...')
            : 'Pick a model first (⌘K)...'}
          rows={1}
          className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed"
          style={{ color: 'var(--color-text)', maxHeight: '200px' }}
          disabled={streamState.isStreaming || modelLoading}
        />
        {streamState.isStreaming ? (
          <button
            onClick={stopStreaming}
            className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer"
            style={{ background: 'var(--color-error)', color: 'var(--color-on-accent)' }}
            title="Stop generating"
          >
            <Square size={16} />
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <MicButton
              state={speechState}
              onClick={handleMicClick}
              voiceAssistant={voiceAssistantEnabled}
            />
            <button
              onClick={() => { void sendMessage(); }}
              disabled={!input.trim() || modelLoading || !selectedModel}
              title={selectedModel ? 'Send message' : 'Pick a model first (⌘K)'}
              className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer disabled:opacity-30 disabled:cursor-default"
              style={{
                background: input.trim() ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                color: input.trim() ? 'white' : 'var(--color-text-tertiary)',
              }}
            >
              <Send size={16} />
            </button>
          </div>
        )}
      </div>
      <div className="flex items-center justify-center mt-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>
          <kbd className="font-mono">Enter</kbd> to send &middot;{' '}
          <kbd className="font-mono">Ctrl+M</kbd> voice &middot;{' '}
          <kbd className="font-mono">Shift+Enter</kbd> for new line
        </span>
      </div>
    </div>
  );
}
