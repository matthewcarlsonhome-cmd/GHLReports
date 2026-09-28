-- 0015: the AM-notes pilot runs without T4 (website capture broken).
--
-- Decision 2026-09-28 (Matthew): form problems are reported by the GHL
-- team, not through AM notes, for now. The six pilot accounts keep T2 leads
-- going cold and T3 customers left waiting. T4 stays in the code's catalog
-- (collector/automation.py NOTIFY_RULES) and its issues are still tracked in
-- alert_state, so adding it back later is this one update in reverse, with
-- history intact. The dashboard and the Monday digest are unchanged.
update public.subaccounts
   set alert_triggers = array_remove(alert_triggers, 'T4')
 where location_id in ('Y4vvMyoOjARnCsb1nEYM',   -- Flohr Pools (Lauren)
                       'A6WeIeAP9Fi2CuCApyce',   -- Central Jersey Pool & Spas (Lauren)
                       'fXNH1f1mo1FxawSUrF4v',   -- Pettis Pools & Patio (Lisa)
                       'WeNxQrw1VO4dRpzEMn7T',   -- Liverpool Pool & Spa (Lisa)
                       'UCq30gKSRj3012SOQVxg',   -- McKinney Custom Pools (Lisa)
                       'aDg77jK5Z8u9NLRVCpHT');  -- AAA Spa & Pool Services (Lisa)
