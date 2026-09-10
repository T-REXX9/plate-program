package com.gatekeeper.simulator;

import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

public final class ServerClient {
    public static final class Result { public final int code; public final long elapsedMs; public final JSONObject body;
        Result(int code, long elapsedMs, JSONObject body) { this.code = code; this.elapsedMs = elapsedMs; this.body = body; } }
    private final String baseUrl, controllerId, controllerKey;
    public ServerClient(String baseUrl, String controllerId, String controllerKey) {
        this.baseUrl = baseUrl.replaceAll("/+\\z", ""); this.controllerId = controllerId; this.controllerKey = controllerKey;
    }
    public Result heartbeat(ControllerState s) throws Exception { Map<String,String> f = fields(s); return post("/api/rfid-controller/status", f); }
    public Result capture(String attempt) throws Exception { Map<String,String> f = new LinkedHashMap<>(); f.put("attempt_uid", attempt); return post("/api/controller/capture-request", f); }
    public Result rfid(String attempt, String value) throws Exception { Map<String,String> f = new LinkedHashMap<>(); f.put("attempt_uid", attempt); f.put("rfid", value); return post("/api/rfid-controller/recognitions", f); }
    public Result accessResult(String attempt) throws Exception { Map<String,String> f = new LinkedHashMap<>(); f.put("attempt_uid", attempt); return post("/api/controller/access-result", f); }
    private Map<String,String> fields(ControllerState s) { Map<String,String> f = new LinkedHashMap<>(); f.put("gate_state", gateState(s)); f.put("rfid_connected", String.valueOf(s.rfidConnected)); f.put("loop_active", String.valueOf(s.loopActive)); f.put("ir_blocked", String.valueOf(s.irBlocked)); f.put("barrier_open", String.valueOf(s.barrierOpen)); f.put("traffic_green", String.valueOf(s.trafficGreen)); f.put("credential_unrecognized", "false"); return f; }
    private static String gateState(ControllerState s) { return s.gate.name().toLowerCase(); }
    private Result post(String path, Map<String,String> fields) throws Exception {
        long start = System.nanoTime(); HttpURLConnection c = (HttpURLConnection) new URL(baseUrl + path).openConnection();
        c.setRequestMethod("POST"); c.setConnectTimeout(10000); c.setReadTimeout(20000); c.setDoOutput(true);
        c.setRequestProperty("Accept", "application/json"); c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
        c.setRequestProperty("X-Controller-Key", controllerKey);
        Map<String,String> all = new LinkedHashMap<>(); all.put("controller_id", controllerId); all.putAll(fields);
        StringBuilder body = new StringBuilder(); for (Map.Entry<String,String> e : all.entrySet()) { if (body.length() > 0) body.append('&'); body.append(URLEncoder.encode(e.getKey(), "UTF-8")).append('=').append(URLEncoder.encode(e.getValue(), "UTF-8")); }
        c.getOutputStream().write(body.toString().getBytes(StandardCharsets.UTF_8));
        int code = c.getResponseCode(); InputStream stream = code >= 400 ? c.getErrorStream() : c.getInputStream();
        StringBuilder text = new StringBuilder(); if (stream != null) { try (BufferedReader r = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) { String line; while ((line = r.readLine()) != null) text.append(line); } }
        return new Result(code, (System.nanoTime() - start) / 1_000_000L, text.length() == 0 ? new JSONObject() : new JSONObject(text.toString()));
    }
}
