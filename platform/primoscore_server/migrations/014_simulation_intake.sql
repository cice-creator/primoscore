ALTER TABLE questionnaires ADD COLUMN simulation_mode TEXT NOT NULL DEFAULT '' CHECK(simulation_mode IN ('','score','max'));
UPDATE questionnaires SET simulation_mode='max' WHERE id IN (SELECT questionnaire_id FROM assessments WHERE engine_version='primoscore-maximum-1');
