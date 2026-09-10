package com.gatekeeper.simulator;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final ControllerState state = new ControllerState();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final StringBuilder timeline = new StringBuilder();
    private TextView gateLabel, sensorLabel, logLabel, resultLabel;
    private EditText server, controller, key, rfid;
    private boolean automatic = true;
    private boolean attemptActive;
    private long attemptStartedAt;
    private static final long AUTHORIZATION_TIMEOUT_MS = 10000L;

    @Override public void onCreate(Bundle saved) { super.onCreate(saved); buildUi(); }
    private TextView label(String value, int size) { TextView v = new TextView(this); v.setText(value); v.setTextSize(size); v.setTextColor(Color.rgb(28,31,39)); v.setPadding(18,12,18,12); return v; }
    private EditText input(String hint, boolean secret) { EditText e = new EditText(this); e.setHint(hint); e.setSingleLine(true); e.setPadding(18,8,18,8); if (secret) e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD); return e; }
    private Button button(String value) { Button b = new Button(this); b.setText(value); b.setAllCaps(false); return b; }
    private void buildUi() {
        ScrollView scroll = new ScrollView(this); LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(18,18,18,30); scroll.addView(root);
        root.addView(label("Plate Controller Simulator", 26)); root.addView(label("STANDALONE · SIMULATION ONLY · NO PHYSICAL OUTPUTS", 12));
        server = input("Server URL (https://...)", false); server.setText("https://server.mlbserver.uk"); root.addView(server);
        controller = input("Provisioned controller ID", false); root.addView(controller);
        key = input("Controller key", true); root.addView(key);
        rfid = input("Optional RFID value", false); root.addView(rfid);
        Button heartbeat = button("Send heartbeat"); root.addView(heartbeat); heartbeat.setOnClickListener(v -> heartbeat());
        gateLabel = label("Gate: IDLE / CLOSED", 20); gateLabel.setTextColor(Color.rgb(41,70,184)); root.addView(gateLabel);
        sensorLabel = label("Loop: CLEAR   IR: CLEAR   Barrier: CLOSED   Traffic: RED", 14); root.addView(sensorLabel);
        LinearLayout controls = new LinearLayout(this); controls.setOrientation(LinearLayout.VERTICAL); root.addView(controls);
        Button loop = button("Vehicle present (inductive loop)"); controls.addView(loop); loop.setOnClickListener(v -> vehiclePresent());
        Button sendRfid = button("Scan RFID now"); controls.addView(sendRfid); sendRfid.setOnClickListener(v -> sendRfid());
        Button clear = button("Vehicle leaves / clear loop"); controls.addView(clear); clear.setOnClickListener(v -> { attemptActive = false; state.loopActive = false; record("SENSOR loop=clear"); render(); });
        Button ir = button("Toggle IR safety beam"); controls.addView(ir); ir.setOnClickListener(v -> { state.irBlocked = !state.irBlocked; record("SENSOR ir_blocked=" + state.irBlocked); render(); });
        Button mode = button("Automatic gate progression: ON"); controls.addView(mode); mode.setOnClickListener(v -> { automatic = !automatic; mode.setText("Automatic gate progression: " + (automatic ? "ON" : "OFF")); record("MODE automatic=" + automatic); });
        Button advance = button("Advance simulated gate state"); controls.addView(advance); advance.setOnClickListener(v -> advanceGate());
        Button retry = button("Retry current vehicle attempt"); controls.addView(retry); retry.setOnClickListener(v -> retryAttempt());
        Button reset = button("Reset simulator"); controls.addView(reset); reset.setOnClickListener(v -> { attemptActive = false; state.reset(); timeline.setLength(0); record("RESET"); render(); });
        resultLabel = label("Server decision: pending", 16); root.addView(resultLabel);
        root.addView(label("Timing timeline", 18)); logLabel = label("Ready. Configure a provisioned Plate + RFID controller.", 13); logLabel.setTextIsSelectable(true); root.addView(logLabel);
        setContentView(scroll); render();
    }
    private ServerClient client() { return new ServerClient(server.getText().toString().trim(), controller.getText().toString().trim(), key.getText().toString()); }
    private void heartbeat() { runRequest("HEARTBEAT", () -> client().heartbeat(state), r -> record("HTTP heartbeat " + r.code + " in " + r.elapsedMs + " ms")); }
    private void vehiclePresent() { if (state.gate != ControllerState.Gate.IDLE_CLOSED) { record("IGNORED loop present while " + state.gate); return; } state.loopActive = true; state.gate = ControllerState.Gate.WAITING_FOR_RFID; state.attemptUid = "sim-" + new SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).format(new Date()) + "-" + UUID.randomUUID().toString().substring(0,8); state.decision = "pending"; attemptActive = true; attemptStartedAt = System.currentTimeMillis(); final String attempt = state.attemptUid; record("SENSOR loop=vehicle_present; attempt=" + attempt); render(); runRequest("CAPTURE", () -> client().capture(attempt), r -> { record("HTTP capture " + r.code + " in " + r.elapsedMs + " ms"); if (!attemptActive || !attempt.equals(state.attemptUid)) return; if (r.code < 200 || r.code >= 300) { record("CAPTURE failed; controller fails closed"); deny(new JSONObject()); return; } state.gate = ControllerState.Gate.RECOGNIZING; render(); pollAuthorization(attempt); }); }
    private void sendRfid() { String value = rfid.getText().toString().trim(); if (value.isEmpty() || !attemptActive || state.attemptUid.isEmpty()) { record("RFID ignored: enter RFID and start a vehicle attempt"); return; } final String attempt = state.attemptUid; runRequest("RFID", () -> client().rfid(attempt, value), r -> record("HTTP RFID " + r.code + " in " + r.elapsedMs + " ms; authorized=" + r.body.optBoolean("authorized", false))); }
    private void pollAuthorization() { pollAuthorization(state.attemptUid); }
    private void pollAuthorization(String attempt) { if (!attemptActive || attempt.isEmpty() || !attempt.equals(state.attemptUid)) return; if (System.currentTimeMillis() - attemptStartedAt >= AUTHORIZATION_TIMEOUT_MS) { record("AUTHORIZATION timeout after 10000 ms; controller fails closed"); deny(new JSONObject()); return; } runRequest("POLL", () -> client().accessResult(attempt), r -> { if (!attemptActive || !attempt.equals(state.attemptUid)) return; String status = r.body.optString("status", "unknown"); record("HTTP poll " + r.code + " in " + r.elapsedMs + " ms → " + status + timing(r.body)); if (r.code < 200 || r.code >= 300) deny(new JSONObject()); else if (status.equals("authorized")) authorize(r.body); else if (status.equals("denied")) deny(r.body); else main.postDelayed(() -> pollAuthorization(attempt), 1000); }); }
    private String timing(JSONObject body) { JSONObject t = body.optJSONObject("server_timing_ms"); return t == null ? "" : "; server queue=" + t.opt("queue") + " ms processing=" + t.opt("processing") + " ms total=" + t.opt("total") + " ms"; }
    private void authorize(JSONObject body) { attemptActive = false; state.decision = "authorized"; state.plate = body.optString("plate", ""); resultLabel.setText("Server decision: AUTHORIZED · " + state.plate); record("AUTHORIZATION authorized; simulated barrier sequence begins"); if (automatic) openingSequence(); else { state.gate = ControllerState.Gate.OPENING; render(); } }
    private void deny(JSONObject body) { attemptActive = false; state.decision = "denied"; state.gate = ControllerState.Gate.DENIED; resultLabel.setText("Server decision: DENIED"); record("AUTHORIZATION denied; barrier remains closed"); render(); }
    private void retryAttempt() { if (!state.loopActive) { record("RETRY ignored: no vehicle at loop"); return; } attemptActive = false; state.gate = ControllerState.Gate.IDLE_CLOSED; state.attemptUid = ""; state.decision = "pending"; record("RETRY starting new vehicle attempt"); vehiclePresent(); }
    private void advanceGate() { if (state.gate == ControllerState.Gate.OPENING) { state.gate = ControllerState.Gate.OPEN; state.barrierOpen = true; state.trafficGreen = true; record("GATE open; traffic=green"); } else if (state.gate == ControllerState.Gate.OPEN) { state.gate = ControllerState.Gate.CLOSING; state.trafficGreen = false; record("GATE closing started"); } else if (state.gate == ControllerState.Gate.CLOSING) { if (state.irBlocked) { state.gate = ControllerState.Gate.OPEN; state.barrierOpen = true; record("SAFETY IR blocked; closing prevented; gate reopened"); } else { state.gate = ControllerState.Gate.IDLE_CLOSED; state.barrierOpen = false; state.loopActive = false; attemptActive = false; record("GATE closed; cycle complete"); } } else { record("GATE step ignored while " + state.gate); } render(); }
    private void openingSequence() { state.gate = ControllerState.Gate.OPENING; state.barrierOpen = false; state.trafficGreen = false; record("GATE opening delay started"); render(); main.postDelayed(() -> { state.gate = ControllerState.Gate.OPEN; state.barrierOpen = true; state.trafficGreen = true; record("GATE open; traffic=green"); render(); main.postDelayed(this::closingSequence, 3000); }, 1200); }
    private void closingSequence() { state.gate = ControllerState.Gate.CLOSING; state.trafficGreen = false; record("GATE closing started"); render(); main.postDelayed(() -> { if (state.irBlocked) { state.gate = ControllerState.Gate.OPEN; state.barrierOpen = true; record("SAFETY IR blocked; closing prevented; gate reopened"); } else { state.gate = ControllerState.Gate.IDLE_CLOSED; state.barrierOpen = false; state.loopActive = false; record("GATE closed; cycle complete"); } render(); }, 1200); }
    private void runRequest(String name, Request request, Complete complete) { record("REQUEST " + name + " started"); network.execute(() -> { try { ServerClient.Result r = request.run(); main.post(() -> complete.done(r)); } catch (Exception e) { main.post(() -> { record("REQUEST " + name + " failed: " + e.getClass().getSimpleName()); state.gate = ControllerState.Gate.FAULT; render(); }); } }); }
    private void record(String line) { timeline.append(new SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(new Date())).append("  ").append(line).append('\n'); if (logLabel != null) logLabel.setText(timeline.toString()); }
    private void render() { if (gateLabel == null) return; gateLabel.setText("Gate: " + state.gate + (state.attemptUid.isEmpty() ? "" : "\nAttempt: " + state.attemptUid)); sensorLabel.setText("Loop: " + (state.loopActive ? "VEHICLE PRESENT" : "CLEAR") + "   IR: " + (state.irBlocked ? "BLOCKED" : "CLEAR") + "\nBarrier: " + (state.barrierOpen ? "OPEN" : "CLOSED") + "   Traffic: " + (state.trafficGreen ? "GREEN" : "RED")); }
    @Override protected void onDestroy() { network.shutdownNow(); super.onDestroy(); }
    private interface Request { ServerClient.Result run() throws Exception; }
    private interface Complete { void done(ServerClient.Result result); }
}
