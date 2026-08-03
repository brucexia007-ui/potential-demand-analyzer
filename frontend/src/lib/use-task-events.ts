"use client";

import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/auth";
import {
  executionEventStreamUrl,
  getExecutionEvents,
  parseExecutionSseFrame,
  type ExecutionEvent,
} from "@/lib/task-execution";

type ConnectionState = "connecting" | "connected" | "reconnecting" | "error";

export function useTaskEvents(taskId: string, initialSequence = 0, enabled = true) {
  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [lastSequence, setLastSequence] = useState(initialSequence);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");

  useEffect(() => {
    if (!taskId || !enabled) return;
    const controller = new AbortController();
    let active = true;
    let sequence = initialSequence;

    const merge = (incoming: ExecutionEvent[]) => {
      if (!incoming.length) return;
      sequence = Math.max(sequence, ...incoming.map((event) => event.sequence));
      setLastSequence(sequence);
      setEvents((current) => {
        const bySequence = new Map(current.map((event) => [event.sequence, event]));
        incoming.forEach((event) => bySequence.set(event.sequence, event));
        return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
      });
    };

    const wait = (milliseconds: number) => new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

    const connect = async () => {
      let retry = 0;
      while (active) {
        try {
          setConnectionState(retry ? "reconnecting" : "connecting");
          const response = await authenticatedFetch(executionEventStreamUrl(taskId, sequence), {
            signal: controller.signal,
          });
          if (!response.ok || !response.body) throw new Error(`任务事件流连接失败 (${response.status})`);
          setConnectionState("connected");
          retry = 0;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (active) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";
            merge(frames.map(parseExecutionSseFrame).filter((event): event is ExecutionEvent => event !== null));
          }
          if (active) {
            retry += 1;
            setConnectionState("reconnecting");
            await wait(Math.min(10_000, 500 * 2 ** retry));
          }
        } catch (error) {
          if (!active || controller.signal.aborted) return;
          try {
            merge(await getExecutionEvents(taskId, sequence));
          } catch {
            setConnectionState("error");
          }
          retry += 1;
          await wait(Math.min(10_000, 500 * 2 ** retry));
        }
      }
    };

    void connect();
    return () => {
      active = false;
      controller.abort();
    };
  }, [taskId, initialSequence, enabled]);

  return { events, lastSequence, connectionState };
}
