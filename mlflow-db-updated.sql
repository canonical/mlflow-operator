-- MySQL dump 10.13  Distrib 8.0.33, for Linux (x86_64)
--
-- Host: 10.1.137.17    Database: database
-- ------------------------------------------------------
-- Server version	5.5.5-10.3.17-MariaDB-1:10.3.17+maria~bionic

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Current Database: `mlflow`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `mlflow` /*!40100 DEFAULT CHARACTER SET latin1 */;

USE `mlflow`;

--
-- Table structure for table `alembic_version`
--

DROP TABLE IF EXISTS `alembic_version`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `alembic_version`
--

LOCK TABLES `alembic_version` WRITE;
/*!40000 ALTER TABLE `alembic_version` DISABLE KEYS */;
INSERT INTO `alembic_version` VALUES ('a8c4a736bde6');
/*!40000 ALTER TABLE `alembic_version` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `experiment_tags`
--

DROP TABLE IF EXISTS `experiment_tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `experiment_tags` (
  `key` varchar(250) NOT NULL,
  `value` varchar(5000) DEFAULT NULL,
  `experiment_id` int(11) NOT NULL,
  PRIMARY KEY (`key`,`experiment_id`),
  KEY `experiment_id` (`experiment_id`),
  CONSTRAINT `experiment_tags_ibfk_1` FOREIGN KEY (`experiment_id`) REFERENCES `experiments` (`experiment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `experiment_tags`
--

LOCK TABLES `experiment_tags` WRITE;
/*!40000 ALTER TABLE `experiment_tags` DISABLE KEYS */;
/*!40000 ALTER TABLE `experiment_tags` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `experiments`
--

DROP TABLE IF EXISTS `experiments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `experiments` (
  `experiment_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(256) NOT NULL,
  `artifact_location` varchar(256) DEFAULT NULL,
  `lifecycle_stage` varchar(32) DEFAULT NULL,
  PRIMARY KEY (`experiment_id`),
  UNIQUE KEY `name` (`name`),
  CONSTRAINT `experiments_lifecycle_stage` CHECK (`lifecycle_stage` in ('active','deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `experiments`
--

LOCK TABLES `experiments` WRITE;
/*!40000 ALTER TABLE `experiments` DISABLE KEYS */;
INSERT INTO `experiments` VALUES (0,'Default','s3://mlflow/0','active');
/*!40000 ALTER TABLE `experiments` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `latest_metrics`
--

DROP TABLE IF EXISTS `latest_metrics`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `latest_metrics` (
  `key` varchar(250) NOT NULL,
  `value` double NOT NULL,
  `timestamp` bigint(20) DEFAULT NULL,
  `step` bigint(20) NOT NULL,
  `is_nan` tinyint(1) NOT NULL,
  `run_uuid` varchar(32) NOT NULL,
  PRIMARY KEY (`key`,`run_uuid`),
  KEY `run_uuid` (`run_uuid`),
  CONSTRAINT `latest_metrics_ibfk_1` FOREIGN KEY (`run_uuid`) REFERENCES `runs` (`run_uuid`),
  CONSTRAINT `CONSTRAINT-1` CHECK (`is_nan` in (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `latest_metrics`
--

LOCK TABLES `latest_metrics` WRITE;
/*!40000 ALTER TABLE `latest_metrics` DISABLE KEYS */;
INSERT INTO `latest_metrics` VALUES ('mae',0.6271946374319586,1690363974097,0,0,'63c8716a595f4a188ce60f535b6fea74'),('r2',0.10862644997792614,1690363974060,0,0,'63c8716a595f4a188ce60f535b6fea74'),('rmse',0.7931640229276851,1690363974041,0,0,'63c8716a595f4a188ce60f535b6fea74');
/*!40000 ALTER TABLE `latest_metrics` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `metrics`
--

DROP TABLE IF EXISTS `metrics`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `metrics` (
  `key` varchar(250) NOT NULL,
  `value` double NOT NULL,
  `timestamp` bigint(20) NOT NULL,
  `run_uuid` varchar(32) NOT NULL,
  `step` bigint(20) NOT NULL DEFAULT 0,
  `is_nan` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`key`,`timestamp`,`step`,`run_uuid`,`value`,`is_nan`),
  KEY `run_uuid` (`run_uuid`),
  CONSTRAINT `metrics_ibfk_1` FOREIGN KEY (`run_uuid`) REFERENCES `runs` (`run_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `metrics`
--

LOCK TABLES `metrics` WRITE;
/*!40000 ALTER TABLE `metrics` DISABLE KEYS */;
INSERT INTO `metrics` VALUES ('mae',0.6271946374319586,1690363974097,'63c8716a595f4a188ce60f535b6fea74',0,0),('r2',0.10862644997792614,1690363974060,'63c8716a595f4a188ce60f535b6fea74',0,0),('rmse',0.7931640229276851,1690363974041,'63c8716a595f4a188ce60f535b6fea74',0,0);
/*!40000 ALTER TABLE `metrics` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `model_version_tags`
--

DROP TABLE IF EXISTS `model_version_tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `model_version_tags` (
  `key` varchar(250) NOT NULL,
  `value` varchar(5000) DEFAULT NULL,
  `name` varchar(256) NOT NULL,
  `version` int(11) NOT NULL,
  PRIMARY KEY (`key`,`name`,`version`),
  KEY `name` (`name`,`version`),
  CONSTRAINT `model_version_tags_ibfk_1` FOREIGN KEY (`name`, `version`) REFERENCES `model_versions` (`name`, `version`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `model_version_tags`
--

LOCK TABLES `model_version_tags` WRITE;
/*!40000 ALTER TABLE `model_version_tags` DISABLE KEYS */;
/*!40000 ALTER TABLE `model_version_tags` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `model_versions`
--

DROP TABLE IF EXISTS `model_versions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `model_versions` (
  `name` varchar(256) NOT NULL,
  `version` int(11) NOT NULL,
  `creation_time` bigint(20) DEFAULT NULL,
  `last_updated_time` bigint(20) DEFAULT NULL,
  `description` varchar(5000) DEFAULT NULL,
  `user_id` varchar(256) DEFAULT NULL,
  `current_stage` varchar(20) DEFAULT NULL,
  `source` varchar(500) DEFAULT NULL,
  `run_id` varchar(32) DEFAULT NULL,
  `status` varchar(20) DEFAULT NULL,
  `status_message` varchar(500) DEFAULT NULL,
  `run_link` varchar(500) DEFAULT NULL,
  PRIMARY KEY (`name`,`version`),
  CONSTRAINT `model_versions_ibfk_1` FOREIGN KEY (`name`) REFERENCES `registered_models` (`name`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `model_versions`
--

LOCK TABLES `model_versions` WRITE;
/*!40000 ALTER TABLE `model_versions` DISABLE KEYS */;
INSERT INTO `model_versions` VALUES ('ElasticnetWineModel2',1,1690363975812,1690363975812,'',NULL,'None','s3://mlflow/0/63c8716a595f4a188ce60f535b6fea74/artifacts/model','63c8716a595f4a188ce60f535b6fea74','READY',NULL,'');
/*!40000 ALTER TABLE `model_versions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `params`
--

DROP TABLE IF EXISTS `params`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `params` (
  `key` varchar(250) NOT NULL,
  `value` varchar(250) NOT NULL,
  `run_uuid` varchar(32) NOT NULL,
  PRIMARY KEY (`key`,`run_uuid`),
  KEY `run_uuid` (`run_uuid`),
  CONSTRAINT `params_ibfk_1` FOREIGN KEY (`run_uuid`) REFERENCES `runs` (`run_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `params`
--

LOCK TABLES `params` WRITE;
/*!40000 ALTER TABLE `params` DISABLE KEYS */;
INSERT INTO `params` VALUES ('alpha','0.5','63c8716a595f4a188ce60f535b6fea74'),('l1_ratio','0.5','63c8716a595f4a188ce60f535b6fea74');
/*!40000 ALTER TABLE `params` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `registered_model_tags`
--

DROP TABLE IF EXISTS `registered_model_tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `registered_model_tags` (
  `key` varchar(250) NOT NULL,
  `value` varchar(5000) DEFAULT NULL,
  `name` varchar(256) NOT NULL,
  PRIMARY KEY (`key`,`name`),
  KEY `name` (`name`),
  CONSTRAINT `registered_model_tags_ibfk_1` FOREIGN KEY (`name`) REFERENCES `registered_models` (`name`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `registered_model_tags`
--

LOCK TABLES `registered_model_tags` WRITE;
/*!40000 ALTER TABLE `registered_model_tags` DISABLE KEYS */;
/*!40000 ALTER TABLE `registered_model_tags` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `registered_models`
--

DROP TABLE IF EXISTS `registered_models`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `registered_models` (
  `name` varchar(256) NOT NULL,
  `creation_time` bigint(20) DEFAULT NULL,
  `last_updated_time` bigint(20) DEFAULT NULL,
  `description` varchar(5000) DEFAULT NULL,
  PRIMARY KEY (`name`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `registered_models`
--

LOCK TABLES `registered_models` WRITE;
/*!40000 ALTER TABLE `registered_models` DISABLE KEYS */;
INSERT INTO `registered_models` VALUES ('ElasticnetWineModel2',1690363975769,1690363975812,'');
/*!40000 ALTER TABLE `registered_models` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `runs`
--

DROP TABLE IF EXISTS `runs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `runs` (
  `run_uuid` varchar(32) NOT NULL,
  `name` varchar(250) DEFAULT NULL,
  `source_type` varchar(20) DEFAULT NULL,
  `source_name` varchar(500) DEFAULT NULL,
  `entry_point_name` varchar(50) DEFAULT NULL,
  `user_id` varchar(256) DEFAULT NULL,
  `status` varchar(9) DEFAULT NULL,
  `start_time` bigint(20) DEFAULT NULL,
  `end_time` bigint(20) DEFAULT NULL,
  `source_version` varchar(50) DEFAULT NULL,
  `lifecycle_stage` varchar(20) DEFAULT NULL,
  `artifact_uri` varchar(200) DEFAULT NULL,
  `experiment_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`run_uuid`),
  KEY `experiment_id` (`experiment_id`),
  CONSTRAINT `runs_ibfk_1` FOREIGN KEY (`experiment_id`) REFERENCES `experiments` (`experiment_id`),
  CONSTRAINT `source_type` CHECK (`source_type` in ('NOTEBOOK','JOB','LOCAL','UNKNOWN','PROJECT')),
  CONSTRAINT `runs_lifecycle_stage` CHECK (`lifecycle_stage` in ('active','deleted')),
  CONSTRAINT `CONSTRAINT_1` CHECK (`status` in ('SCHEDULED','FAILED','FINISHED','RUNNING','KILLED'))
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `runs`
--

LOCK TABLES `runs` WRITE;
/*!40000 ALTER TABLE `runs` DISABLE KEYS */;
INSERT INTO `runs` VALUES ('63c8716a595f4a188ce60f535b6fea74','','UNKNOWN','','','pocik','FINISHED',1690363973958,1690363975828,'','active','s3://mlflow/0/63c8716a595f4a188ce60f535b6fea74/artifacts',0);
/*!40000 ALTER TABLE `runs` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `tags`
--

DROP TABLE IF EXISTS `tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tags` (
  `key` varchar(250) NOT NULL,
  `value` varchar(5000) DEFAULT NULL,
  `run_uuid` varchar(32) NOT NULL,
  PRIMARY KEY (`key`,`run_uuid`),
  KEY `run_uuid` (`run_uuid`),
  CONSTRAINT `tags_ibfk_1` FOREIGN KEY (`run_uuid`) REFERENCES `runs` (`run_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tags`
--

LOCK TABLES `tags` WRITE;
/*!40000 ALTER TABLE `tags` DISABLE KEYS */;
INSERT INTO `tags` VALUES ('mlflow.log-model.history','[{\"run_id\": \"63c8716a595f4a188ce60f535b6fea74\", \"artifact_path\": \"model\", \"utc_time_created\": \"2023-07-26 09:32:54.118795\", \"flavors\": {\"python_function\": {\"model_path\": \"model.pkl\", \"predict_fn\": \"predict\", \"loader_module\": \"mlflow.sklearn\", \"python_version\": \"3.8.0\", \"env\": {\"conda\": \"conda.yaml\", \"virtualenv\": \"python_env.yaml\"}}, \"sklearn\": {\"pickled_model\": \"model.pkl\", \"sklearn_version\": \"1.2.2\", \"serialization_format\": \"cloudpickle\", \"code\": null}}, \"model_uuid\": \"3cdc101199324c5f89ca72442a30955a\", \"mlflow_version\": \"2.4.0\"}]','63c8716a595f4a188ce60f535b6fea74'),('mlflow.source.git.commit','572a8bcfbae62fcfce6b817484951f3df8b9be85','63c8716a595f4a188ce60f535b6fea74'),('mlflow.source.name','/home/pocik/.pyenv/versions/3.8.0/envs/pip-compile8/lib/python3.8/site-packages/ipykernel_launcher.py','63c8716a595f4a188ce60f535b6fea74'),('mlflow.source.type','LOCAL','63c8716a595f4a188ce60f535b6fea74'),('mlflow.user','pocik','63c8716a595f4a188ce60f535b6fea74');
/*!40000 ALTER TABLE `tags` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2023-07-26 11:41:33
