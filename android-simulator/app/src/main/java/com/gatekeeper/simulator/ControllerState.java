package com.gatekeeper.simulator;

public final class ControllerState {
    public enum Gate { IDLE_CLOSED, WAITING_FOR_RFID, RECOGNIZING, OPENING, OPEN, CLOSING, FAULT, DENIED }

    public Gate gate = Gate.IDLE_CLOSED;
    public boolean loopActive;
    public boolean irBlocked;
    public boolean barrierOpen;
    public boolean trafficGreen;
    public boolean rfidConnected = true;
    public String attemptUid = "";
    public String decision = "pending";
    public String plate = "";

    public void reset() {
        gate = Gate.IDLE_CLOSED; loopActive = false; irBlocked = false;
        barrierOpen = false; trafficGreen = false; attemptUid = "";
        decision = "pending"; plate = "";
    }
}
