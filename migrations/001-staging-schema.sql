-- Festgelegtes SQLite-Schema für synthetische Staging-Daten (12.10.2026).

CREATE TABLE addresses (
	id INTEGER NOT NULL, 
	street_id INTEGER NOT NULL, 
	house_number VARCHAR NOT NULL, 
	latitude FLOAT NOT NULL, 
	longitude FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_address_street_house UNIQUE (street_id, house_number), 
	FOREIGN KEY(street_id) REFERENCES streets (id)
);

CREATE TABLE street_segments (
	id INTEGER NOT NULL, 
	street_id INTEGER NOT NULL, 
	start_lat FLOAT NOT NULL, 
	start_lon FLOAT NOT NULL, 
	end_lat FLOAT NOT NULL, 
	end_lon FLOAT NOT NULL, 
	min_lat FLOAT NOT NULL, 
	max_lat FLOAT NOT NULL, 
	min_lon FLOAT NOT NULL, 
	max_lon FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_street_segment UNIQUE (street_id, start_lat, start_lon, end_lat, end_lon), 
	FOREIGN KEY(street_id) REFERENCES streets (id)
);

CREATE VIRTUAL TABLE street_spellfix USING spellfix1;

CREATE VIRTUAL TABLE street_trigram USING fts5(normalized_name, content='', tokenize='trigram');

CREATE TABLE streets (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	normalized_name VARCHAR NOT NULL, 
	city VARCHAR NOT NULL, 
	postal_code VARCHAR, 
	regional_key VARCHAR, 
	borough VARCHAR, 
	suburb VARCHAR, 
	latitude FLOAT NOT NULL, 
	longitude FLOAT NOT NULL, 
	normalized_search VARCHAR NOT NULL, 
	phonetic_german VARCHAR NOT NULL, 
	phonetic_cologne VARCHAR NOT NULL, 
	consonant_key VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_street_name_city UNIQUE (name, postal_code, city)
);

CREATE INDEX idx_address_location ON addresses (latitude, longitude);

CREATE INDEX idx_segment_bbox ON street_segments (min_lat, max_lat, min_lon, max_lon);

CREATE INDEX idx_segment_lat_range ON street_segments (min_lat, max_lat);

CREATE INDEX idx_segment_lon_range ON street_segments (min_lon, max_lon);

CREATE INDEX idx_segment_street ON street_segments (street_id);

CREATE INDEX idx_segment_street_id ON street_segments (street_id);

CREATE INDEX idx_street_city_lower ON streets (LOWER(city));

CREATE INDEX idx_street_consonant_key ON streets (consonant_key);

CREATE INDEX idx_street_house ON addresses (street_id, house_number);

CREATE INDEX idx_street_location ON streets (latitude, longitude);

CREATE INDEX idx_street_name_city ON streets (name, city);

CREATE INDEX idx_street_normalized_name ON streets (normalized_name);

CREATE INDEX idx_street_normalized_search ON streets (normalized_search);

CREATE INDEX idx_street_phonetic_cologne ON streets (phonetic_cologne);

CREATE INDEX idx_street_phonetic_german ON streets (phonetic_german);

CREATE INDEX ix_addresses_house_number ON addresses (house_number);

CREATE INDEX ix_addresses_id ON addresses (id);

CREATE INDEX ix_addresses_street_id ON addresses (street_id);

CREATE INDEX ix_street_segments_id ON street_segments (id);

CREATE INDEX ix_street_segments_max_lat ON street_segments (max_lat);

CREATE INDEX ix_street_segments_max_lon ON street_segments (max_lon);

CREATE INDEX ix_street_segments_min_lat ON street_segments (min_lat);

CREATE INDEX ix_street_segments_min_lon ON street_segments (min_lon);

CREATE INDEX ix_street_segments_street_id ON street_segments (street_id);

CREATE INDEX ix_streets_borough ON streets (borough);

CREATE INDEX ix_streets_city ON streets (city);

CREATE INDEX ix_streets_consonant_key ON streets (consonant_key);

CREATE INDEX ix_streets_id ON streets (id);

CREATE INDEX ix_streets_name ON streets (name);

CREATE INDEX ix_streets_normalized_name ON streets (normalized_name);

CREATE INDEX ix_streets_normalized_search ON streets (normalized_search);

CREATE INDEX ix_streets_phonetic_cologne ON streets (phonetic_cologne);

CREATE INDEX ix_streets_phonetic_german ON streets (phonetic_german);

CREATE INDEX ix_streets_postal_code ON streets (postal_code);

CREATE INDEX ix_streets_regional_key ON streets (regional_key);

CREATE INDEX ix_streets_suburb ON streets (suburb);
