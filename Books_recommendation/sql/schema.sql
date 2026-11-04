-- Schéma du module livres (base recommendation_mvp, MySQL/MariaDB XAMPP).
-- Reproduit exactement les tables existantes. Non destructif :
-- CREATE TABLE IF NOT EXISTS ne modifie ni ne vide une table déjà présente.
--
-- Application (PowerShell, depuis la racine du projet) :
--   cmd /c "C:\xampp\mysql\bin\mysql.exe -u root < Books_recommendation\sql\schema.sql"

CREATE DATABASE IF NOT EXISTS recommendation_mvp
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE recommendation_mvp;

CREATE TABLE IF NOT EXISTS `books` (
  `book_id` varchar(20) NOT NULL,
  `isbn10` varchar(20) DEFAULT NULL,
  `title` text NOT NULL,
  `subtitle` text DEFAULT NULL,
  `authors` text DEFAULT NULL,
  `categories` text DEFAULT NULL,
  `thumbnail` text DEFAULT NULL,
  `description` longtext NOT NULL,
  `content` longtext NOT NULL,
  `published_year` smallint(6) DEFAULT NULL,
  `average_rating` decimal(4,2) DEFAULT NULL,
  `num_pages` int(10) unsigned DEFAULT NULL,
  `ratings_count` bigint(20) unsigned DEFAULT NULL,
  PRIMARY KEY (`book_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
;

CREATE TABLE IF NOT EXISTS `book_sessions` (
  `session_id` char(36) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `last_seen_at` datetime(6) NOT NULL,
  PRIMARY KEY (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
;

CREATE TABLE IF NOT EXISTS `book_selections` (
  `selection_id` char(36) NOT NULL,
  `session_id` char(36) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`selection_id`),
  KEY `idx_selection_session` (`session_id`,`created_at`),
  CONSTRAINT `fk_book_selection_session` FOREIGN KEY (`session_id`) REFERENCES `book_sessions` (`session_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
;

CREATE TABLE IF NOT EXISTS `book_selection_items` (
  `selection_id` char(36) NOT NULL,
  `book_id` varchar(20) NOT NULL,
  `position` tinyint(3) unsigned NOT NULL,
  PRIMARY KEY (`selection_id`,`book_id`),
  UNIQUE KEY `uq_selection_position` (`selection_id`,`position`),
  KEY `fk_selection_item_book` (`book_id`),
  CONSTRAINT `fk_selection_item_book` FOREIGN KEY (`book_id`) REFERENCES `books` (`book_id`),
  CONSTRAINT `fk_selection_item_selection` FOREIGN KEY (`selection_id`) REFERENCES `book_selections` (`selection_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
;

CREATE TABLE IF NOT EXISTS `book_events` (
  `event_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `request_id` char(36) NOT NULL,
  `session_id` char(36) NOT NULL,
  `book_id` varchar(20) NOT NULL,
  `event_type` enum('open','like','unlike','dismiss','undismiss') NOT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`event_id`),
  UNIQUE KEY `uq_book_event_request` (`session_id`,`request_id`),
  KEY `idx_event_session_book` (`session_id`,`book_id`,`event_id`),
  KEY `fk_book_event_book` (`book_id`),
  CONSTRAINT `fk_book_event_book` FOREIGN KEY (`book_id`) REFERENCES `books` (`book_id`),
  CONSTRAINT `fk_book_event_session` FOREIGN KEY (`session_id`) REFERENCES `book_sessions` (`session_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
;

