BEGIN TRANSACTION;
CREATE TABLE audit (
    ord INTEGER PRIMARY KEY, op TEXT NOT NULL, memory_id TEXT NOT NULL,
    layer TEXT NOT NULL, before_sha TEXT NOT NULL, after_sha TEXT NOT NULL,
    reason TEXT NOT NULL, entry_sha TEXT NOT NULL
);
INSERT INTO "audit" VALUES(7,'update','ccdac4925bc60fa1','L1','911d46ecae1bc623b89f4771f5238a5f29e3b8f5017304902b5c4cee52f8d216','e019b490c5bd4c30355a92031aa8d9c4f67d5e91ed8fbf20c24d64520c9848d2','more precise','b6595b82c4de24eedd979530a95e534f49e688b799eba647cb4dea8849f97581');
INSERT INTO "audit" VALUES(10,'supersede','b9ee9b8000023a20','L1','0081986843cda12228a073a962d21176fea6c11461c229572b6abfecc54bce72','','changed taste','cfac05ee48792a5ff2d3cf5252a7136e299999806577033eccf1de1b052be33e');
INSERT INTO "audit" VALUES(11,'forget','2f8d498ae8fa1381','L1','14272f73ca12c2b7c0208eae07988b1a92c842c169e9cff7d225b0e34b6bfb01','','user asked','ddeb169cf300a70149594571d94d7839e59d94fcbedfeadae86ff3dace0ab4be');
CREATE TABLE memories (
    id TEXT PRIMARY KEY, layer TEXT NOT NULL, session TEXT, "user" TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, source_ids TEXT NOT NULL, extractor TEXT NOT NULL,
    criterion TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_ord INTEGER NOT NULL,
    valid_until INTEGER, superseded_by TEXT,
    source_hashes TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO "memories" VALUES('1ee741684b9461f8','L1','s','','My name is Dana.','["t1"]','rule/v1','atomic user fact','0a40f8726c9a5d78450681d756645e535f89228b852e89102285cf5929396e15',3,NULL,NULL,'{"t1": "90678b11230926112dd3bfc6dc27ff705ed742e7ad39cdb18137db7c74948990"}');
INSERT INTO "memories" VALUES('ccdac4925bc60fa1','L1','s','','I live in Denver, CO.','["t1"]','rule/v1','atomic user fact','e019b490c5bd4c30355a92031aa8d9c4f67d5e91ed8fbf20c24d64520c9848d2',4,NULL,NULL,'{"t1": "90678b11230926112dd3bfc6dc27ff705ed742e7ad39cdb18137db7c74948990"}');
INSERT INTO "memories" VALUES('b9ee9b8000023a20','L1','s','','I prefer dark roast coffee.','["t2"]','rule/v1','atomic user fact','0081986843cda12228a073a962d21176fea6c11461c229572b6abfecc54bce72',5,9,'ca90978755582fc1','{"t2": "7ad139e8f4e2ff95236dbdb94a19bf1d6b40c953c481b56c891e689a080fb470"}');
INSERT INTO "memories" VALUES('ca90978755582fc1','L1','s','','I prefer green tea.','["b9ee9b8000023a20"]','supersede/v1','supersedes b9ee9b8000023a20','a974b2eb02bf385a2041bd388fc338bb4f69f0e099f795639beaa5723b5e8283',8,NULL,NULL,'{"b9ee9b8000023a20": "0081986843cda12228a073a962d21176fea6c11461c229572b6abfecc54bce72"}');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('schema_version','4');
INSERT INTO "meta" VALUES('ord','11');
INSERT INTO "meta" VALUES('audit_count','3');
INSERT INTO "meta" VALUES('audit_head','ddeb169cf300a70149594571d94d7839e59d94fcbedfeadae86ff3dace0ab4be');
CREATE TABLE turns (
    id TEXT PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, ord INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT ''
);
INSERT INTO "turns" VALUES('t1','s','user','My name is Dana. I live in Denver.',0,'90678b11230926112dd3bfc6dc27ff705ed742e7ad39cdb18137db7c74948990','');
INSERT INTO "turns" VALUES('t2','s','user','I prefer dark roast coffee.',1,'7ad139e8f4e2ff95236dbdb94a19bf1d6b40c953c481b56c891e689a080fb470','');
INSERT INTO "turns" VALUES('t3','s','user','I work as a night nurse at the clinic.',2,'4e5a58601b516aa5f76294a968b35af60d9a91b5677d2244f93b057946b48d93','');
CREATE INDEX idx_mem_layer ON memories(layer);
CREATE INDEX idx_mem_session ON memories(session);
CREATE INDEX idx_mem_user ON memories("user");
COMMIT;
