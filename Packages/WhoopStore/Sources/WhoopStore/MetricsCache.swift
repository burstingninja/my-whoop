import Foundation
import GRDB

// MARK: - Offline cache of SERVER-computed metrics (Task 3.1 → M0.4)
// This file is purely a local cache of values computed by the server — the phone does NO metric
// computation here. DailyMetric and CachedSleepSession mirror the server's daily_metrics /
// sleep_sessions tables and are cached locally so History = union(phone-collected raw streams,
// server-computed derived metrics). ServerSync.pull() populates this cache; MetricsRepository
// reads it for the view layer.

/// One cached sleep session pulled from the server's /v1/sleep. Natural key (deviceId, startTs).
/// `stagesJSON` is the verbatim JSON array of stage segments ([{start,end,stage}]) — stored as a
/// string so the cache stays schema-agnostic about the staging shape.
public struct CachedSleepSession: Equatable, Codable {
    public let startTs: Int          // unix seconds
    public let endTs: Int            // unix seconds
    public let efficiency: Double?
    public let restingHr: Int?
    public let avgHrv: Double?
    public let stagesJSON: String?
    public init(startTs: Int, endTs: Int, efficiency: Double?, restingHr: Int?,
                avgHrv: Double?, stagesJSON: String?) {
        self.startTs = startTs; self.endTs = endTs
        self.efficiency = efficiency; self.restingHr = restingHr
        self.avgHrv = avgHrv; self.stagesJSON = stagesJSON
    }
}

/// One cached daily-metrics row pulled from the server's /v1/daily. Natural key (deviceId, day).
public struct DailyMetric: Equatable, Codable {
    public let day: String           // YYYY-MM-DD
    public let totalSleepMin: Double?
    public let efficiency: Double?
    public let deepMin: Double?
    public let remMin: Double?
    public let lightMin: Double?
    public let disturbances: Int?
    public let restingHr: Int?
    public let avgHrv: Double?
    public let recovery: Double?
    public let strain: Double?
    public let exerciseCount: Int?
    // In-sleep signal aggregates (v7 columns). All nullable; computed server-side.
    public let spo2Pct: Double?
    public let skinTempDevC: Double?
    public let respRateBpm: Double?
    // Goose-complement metrics (v8 columns). All nullable; computed server-side.
    public let stressScore: Double?       // waking stress 0–100
    public let stressHighMin: Double?     // minutes in high stress
    public let stressMidMin: Double?      // minutes in medium stress
    public let stressLowMin: Double?      // minutes in low stress
    public let sleepNeedMin: Double?      // tonight's sleep need (target + strain surcharge)
    public let sleepDebtMin: Double?      // 14-night rolling debt (positive = behind)
    public let sleepBankMin: Double?      // surplus component of debt (0 when in deficit)
    public let sleepPerformance: Double?  // actual / need, 0–1
    public let hrDipPct: Double?          // % drop from pre-sleep HR to nightly floor
    public let restorativeMin: Double?    // deep + REM minutes combined
    public let wasoMin: Double?           // wake-after-sleep-onset minutes
    public let sleepLatencyMin: Double?   // minutes to fall asleep
    public let energyScore: Double?       // 7-day rolling energy bank 0–100
    public let zoneMinutesJSON: String?   // {"1":min,"2":min,…,"5":min} from Edwards zones

    public init(day: String, totalSleepMin: Double?, efficiency: Double?, deepMin: Double?,
                remMin: Double?, lightMin: Double?, disturbances: Int?, restingHr: Int?,
                avgHrv: Double?, recovery: Double?, strain: Double?, exerciseCount: Int?,
                spo2Pct: Double? = nil, skinTempDevC: Double? = nil, respRateBpm: Double? = nil,
                stressScore: Double? = nil, stressHighMin: Double? = nil,
                stressMidMin: Double? = nil, stressLowMin: Double? = nil,
                sleepNeedMin: Double? = nil, sleepDebtMin: Double? = nil,
                sleepBankMin: Double? = nil, sleepPerformance: Double? = nil,
                hrDipPct: Double? = nil, restorativeMin: Double? = nil,
                wasoMin: Double? = nil, sleepLatencyMin: Double? = nil,
                energyScore: Double? = nil, zoneMinutesJSON: String? = nil) {
        self.day = day; self.totalSleepMin = totalSleepMin; self.efficiency = efficiency
        self.deepMin = deepMin; self.remMin = remMin; self.lightMin = lightMin
        self.disturbances = disturbances; self.restingHr = restingHr; self.avgHrv = avgHrv
        self.recovery = recovery; self.strain = strain; self.exerciseCount = exerciseCount
        self.spo2Pct = spo2Pct; self.skinTempDevC = skinTempDevC; self.respRateBpm = respRateBpm
        self.stressScore = stressScore; self.stressHighMin = stressHighMin
        self.stressMidMin = stressMidMin; self.stressLowMin = stressLowMin
        self.sleepNeedMin = sleepNeedMin; self.sleepDebtMin = sleepDebtMin
        self.sleepBankMin = sleepBankMin; self.sleepPerformance = sleepPerformance
        self.hrDipPct = hrDipPct; self.restorativeMin = restorativeMin
        self.wasoMin = wasoMin; self.sleepLatencyMin = sleepLatencyMin
        self.energyScore = energyScore; self.zoneMinutesJSON = zoneMinutesJSON
    }
}

extension WhoopStore {

    // MARK: - Upserts (idempotent by natural key; latest server value wins on conflict)

    /// Upsert cached sleep sessions. Natural key (deviceId, startTs). Returns rows changed.
    @discardableResult
    public func upsertSleepSessions(_ sessions: [CachedSleepSession], deviceId: String) async throws -> Int {
        try syncWrite { db in
            var n = 0
            for s in sessions {
                try db.execute(sql: """
                    INSERT INTO sleepSession
                        (deviceId, startTs, endTs, efficiency, restingHr, avgHrv, stagesJSON)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(deviceId, startTs) DO UPDATE SET
                        endTs = excluded.endTs,
                        efficiency = excluded.efficiency,
                        restingHr = excluded.restingHr,
                        avgHrv = excluded.avgHrv,
                        stagesJSON = excluded.stagesJSON
                    """, arguments: [deviceId, s.startTs, s.endTs, s.efficiency,
                                     s.restingHr, s.avgHrv, s.stagesJSON])
                n += db.changesCount
            }
            return n
        }
    }

    /// Upsert cached daily metrics. Natural key (deviceId, day). Returns rows changed.
    @discardableResult
    public func upsertDailyMetrics(_ days: [DailyMetric], deviceId: String) async throws -> Int {
        try syncWrite { db in
            var n = 0
            for d in days {
                try db.execute(sql: """
                    INSERT INTO dailyMetric
                        (deviceId, day, totalSleepMin, efficiency, deepMin, remMin, lightMin,
                         disturbances, restingHr, avgHrv, recovery, strain, exerciseCount,
                         spo2Pct, skinTempDevC, respRateBpm,
                         stressScore, stressHighMin, stressMidMin, stressLowMin,
                         sleepNeedMin, sleepDebtMin, sleepBankMin, sleepPerformance,
                         hrDipPct, restorativeMin, wasoMin, sleepLatencyMin,
                         energyScore, zoneMinutesJSON)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(deviceId, day) DO UPDATE SET
                        totalSleepMin = excluded.totalSleepMin,
                        efficiency = excluded.efficiency,
                        deepMin = excluded.deepMin,
                        remMin = excluded.remMin,
                        lightMin = excluded.lightMin,
                        disturbances = excluded.disturbances,
                        restingHr = excluded.restingHr,
                        avgHrv = excluded.avgHrv,
                        recovery = excluded.recovery,
                        strain = excluded.strain,
                        exerciseCount = excluded.exerciseCount,
                        spo2Pct = excluded.spo2Pct,
                        skinTempDevC = excluded.skinTempDevC,
                        respRateBpm = excluded.respRateBpm,
                        stressScore = excluded.stressScore,
                        stressHighMin = excluded.stressHighMin,
                        stressMidMin = excluded.stressMidMin,
                        stressLowMin = excluded.stressLowMin,
                        sleepNeedMin = excluded.sleepNeedMin,
                        sleepDebtMin = excluded.sleepDebtMin,
                        sleepBankMin = excluded.sleepBankMin,
                        sleepPerformance = excluded.sleepPerformance,
                        hrDipPct = excluded.hrDipPct,
                        restorativeMin = excluded.restorativeMin,
                        wasoMin = excluded.wasoMin,
                        sleepLatencyMin = excluded.sleepLatencyMin,
                        energyScore = excluded.energyScore,
                        zoneMinutesJSON = excluded.zoneMinutesJSON
                    """, arguments: [deviceId, d.day, d.totalSleepMin, d.efficiency, d.deepMin,
                                     d.remMin, d.lightMin, d.disturbances, d.restingHr, d.avgHrv,
                                     d.recovery, d.strain, d.exerciseCount,
                                     d.spo2Pct, d.skinTempDevC, d.respRateBpm,
                                     d.stressScore, d.stressHighMin, d.stressMidMin, d.stressLowMin,
                                     d.sleepNeedMin, d.sleepDebtMin, d.sleepBankMin, d.sleepPerformance,
                                     d.hrDipPct, d.restorativeMin, d.wasoMin, d.sleepLatencyMin,
                                     d.energyScore, d.zoneMinutesJSON])
                n += db.changesCount
            }
            return n
        }
    }

    // MARK: - Reads

    /// Cached sleep sessions overlapping [from, to] (by startTs), oldest first.
    public func sleepSessions(deviceId: String, from: Int, to: Int, limit: Int) async throws -> [CachedSleepSession] {
        try syncRead { db in
            try Row.fetchAll(db, sql: """
                SELECT startTs, endTs, efficiency, restingHr, avgHrv, stagesJSON FROM sleepSession
                WHERE deviceId = ? AND startTs >= ? AND startTs <= ?
                ORDER BY startTs ASC LIMIT ?
                """, arguments: [deviceId, from, to, limit])
                .map {
                    CachedSleepSession(startTs: $0["startTs"], endTs: $0["endTs"],
                                       efficiency: $0["efficiency"], restingHr: $0["restingHr"],
                                       avgHrv: $0["avgHrv"], stagesJSON: $0["stagesJSON"])
                }
        }
    }

    /// Cached daily metrics for days in [from, to] (lexicographic YYYY-MM-DD compare), oldest first.
    public func dailyMetrics(deviceId: String, from: String, to: String) async throws -> [DailyMetric] {
        try syncRead { db in
            try Row.fetchAll(db, sql: """
                SELECT day, totalSleepMin, efficiency, deepMin, remMin, lightMin, disturbances,
                       restingHr, avgHrv, recovery, strain, exerciseCount,
                       spo2Pct, skinTempDevC, respRateBpm,
                       stressScore, stressHighMin, stressMidMin, stressLowMin,
                       sleepNeedMin, sleepDebtMin, sleepBankMin, sleepPerformance,
                       hrDipPct, restorativeMin, wasoMin, sleepLatencyMin,
                       energyScore, zoneMinutesJSON
                FROM dailyMetric
                WHERE deviceId = ? AND day >= ? AND day <= ?
                ORDER BY day ASC
                """, arguments: [deviceId, from, to])
                .map {
                    DailyMetric(day: $0["day"], totalSleepMin: $0["totalSleepMin"],
                                efficiency: $0["efficiency"], deepMin: $0["deepMin"],
                                remMin: $0["remMin"], lightMin: $0["lightMin"],
                                disturbances: $0["disturbances"], restingHr: $0["restingHr"],
                                avgHrv: $0["avgHrv"], recovery: $0["recovery"],
                                strain: $0["strain"], exerciseCount: $0["exerciseCount"],
                                spo2Pct: $0["spo2Pct"], skinTempDevC: $0["skinTempDevC"],
                                respRateBpm: $0["respRateBpm"],
                                stressScore: $0["stressScore"], stressHighMin: $0["stressHighMin"],
                                stressMidMin: $0["stressMidMin"], stressLowMin: $0["stressLowMin"],
                                sleepNeedMin: $0["sleepNeedMin"], sleepDebtMin: $0["sleepDebtMin"],
                                sleepBankMin: $0["sleepBankMin"], sleepPerformance: $0["sleepPerformance"],
                                hrDipPct: $0["hrDipPct"], restorativeMin: $0["restorativeMin"],
                                wasoMin: $0["wasoMin"], sleepLatencyMin: $0["sleepLatencyMin"],
                                energyScore: $0["energyScore"], zoneMinutesJSON: $0["zoneMinutesJSON"])
                }
        }
    }
}
