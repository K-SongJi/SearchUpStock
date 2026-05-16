CREATE DATABASE IF NOT EXISTS stock_analysis
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE stock_analysis;

CREATE TABLE IF NOT EXISTS upload_batches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  file_name VARCHAR(255) NOT NULL,
  file_hash CHAR(64) NOT NULL,
  detected_date DATE NULL,
  uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status VARCHAR(30) NOT NULL DEFAULT 'success',
  total_rows INT UNSIGNED NOT NULL DEFAULT 0,
  valid_rows INT UNSIGNED NOT NULL DEFAULT 0,
  duplicate_rows INT UNSIGNED NOT NULL DEFAULT 0,
  memo TEXT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_upload_batches_file_hash (file_hash),
  KEY idx_upload_batches_detected_date (detected_date),
  KEY idx_upload_batches_uploaded_at (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS kiwoom_condition_results (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  upload_batch_id BIGINT UNSIGNED NOT NULL,
  detected_date DATE NOT NULL,
  stock_code VARCHAR(20) NOT NULL,
  stock_name VARCHAR(100) NULL,
  current_price DECIMAL(18, 4) NULL,
  day_direction VARCHAR(20) NULL,
  day_change_price DECIMAL(18, 4) NULL,
  day_change_rate DECIMAL(10, 4) NULL,
  volume BIGINT UNSIGNED NULL,
  reference_text TEXT NULL,
  row_order INT UNSIGNED NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_kiwoom_detected_stock (detected_date, stock_code),
  KEY idx_kiwoom_upload_batch (upload_batch_id),
  KEY idx_kiwoom_stock_code (stock_code),
  CONSTRAINT fk_kiwoom_upload_batch
    FOREIGN KEY (upload_batch_id)
    REFERENCES upload_batches (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_analysis_results (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  upload_batch_id BIGINT UNSIGNED NOT NULL,
  detected_date DATE NOT NULL,
  stock_code VARCHAR(20) NOT NULL,
  stock_name VARCHAR(100) NULL,
  condition_type VARCHAR(50) NOT NULL DEFAULT 'unspecified',
  close_price DECIMAL(18, 4) NULL,
  pre_filter_score INT NULL,
  final_score INT NULL,
  final_grade VARCHAR(50) NULL,
  volume_ratio DECIMAL(12, 4) NULL,
  rsi DECIMAL(12, 4) NULL,
  disparity DECIMAL(12, 4) NULL,
  row_order INT UNSIGNED NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_analysis_detected_stock_condition (detected_date, stock_code, condition_type),
  KEY idx_analysis_upload_batch (upload_batch_id),
  KEY idx_analysis_stock_code (stock_code),
  KEY idx_analysis_score (final_score),
  KEY idx_analysis_grade (final_grade),
  KEY idx_analysis_condition_type (condition_type),
  CONSTRAINT fk_analysis_upload_batch
    FOREIGN KEY (upload_batch_id)
    REFERENCES upload_batches (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS next_day_outcomes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  upload_batch_id BIGINT UNSIGNED NOT NULL,
  detected_date DATE NOT NULL,
  result_date DATE NULL,
  stock_code VARCHAR(20) NOT NULL,
  stock_name VARCHAR(100) NULL,
  base_price DECIMAL(18, 4) NULL,
  next_price DECIMAL(18, 4) NULL,
  price_diff DECIMAL(18, 4) NULL,
  result_status VARCHAR(30) NULL,
  return_rate DECIMAL(10, 4) NULL,
  row_order INT UNSIGNED NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_outcome_detected_stock (detected_date, stock_code),
  KEY idx_outcome_upload_batch (upload_batch_id),
  KEY idx_outcome_stock_code (stock_code),
  KEY idx_outcome_result_date (result_date),
  KEY idx_outcome_return_rate (return_rate),
  CONSTRAINT fk_outcome_upload_batch
    FOREIGN KEY (upload_batch_id)
    REFERENCES upload_batches (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
