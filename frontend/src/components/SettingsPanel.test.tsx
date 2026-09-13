import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import SettingsPanel from "./SettingsPanel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("SettingsPanel debounce", () => {
  it("remonte la valeur au parent une seule fois après le glissement", () => {
    vi.useFakeTimers();
    const onIntervalChange = vi.fn();
    render(
      <SettingsPanel
        intervalMs={3000}
        onIntervalChange={onIntervalChange}
        running
        onToggleRunning={() => {}}
        ipCamera={{ enabled: false, url: "http://192.168.1.43/image/jpeg.cgi" }}
        onIpCameraChange={() => {}}
      />,
    );

    const slider = screen.getByLabelText(/Intervalle de capture/i);
    // Plusieurs ticks rapprochés pendant le glissement.
    fireEvent.change(slider, { target: { value: "4000" } });
    fireEvent.change(slider, { target: { value: "5000" } });
    fireEvent.change(slider, { target: { value: "6000" } });

    // Avant le délai de debounce : aucun appel parent.
    expect(onIntervalChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(300);

    // Un seul appel, avec la dernière valeur.
    expect(onIntervalChange).toHaveBeenCalledTimes(1);
    expect(onIntervalChange).toHaveBeenCalledWith(6000);
  });
});
