create table PUBLIC.`object`
(
    uuid     VARCHAR(255) primary key,
    `object` CHARACTER LARGE OBJECT
);

create table PUBLIC.`settings`
(
    kind               VARCHAR(255) primary key,
    `enable-profiling` BOOLEAN,
    `subscription-url` VARCHAR(1000),
    `user-email`       VARCHAR(255),
    `version`          VARCHAR(255)
);

create table PUBLIC.`feed`
(
    uuid        VARCHAR(255) primary key,
    `title`     VARCHAR(255),
    `suffix`    VARCHAR(255),
    `source`    CHARACTER LARGE OBJECT,
    `proxy`     VARCHAR(255),
    `charset`   VARCHAR(255),
    `output`    VARCHAR(255),
    `group`     VARCHAR(255),
    `task`      VARCHAR(255),
    `parallel`  BOOLEAN,
    `timeout`   INTEGER,
    `selectors` CHARACTER LARGE OBJECT,
    `pages`     CHARACTER LARGE OBJECT,
    `filter`    CHARACTER LARGE OBJECT,
    `realtime`  BOOLEAN,
    `extractor` VARCHAR(255),
    `partition` INTEGER,
    `params`    CHARACTER LARGE OBJECT,
    `_extra`   CHARACTER LARGE OBJECT
);

create table PUBLIC.`feed-definition`
(
    uuid   VARCHAR(255) primary key,
    `yaml` CHARACTER LARGE OBJECT
);

create table PUBLIC.`image`
(
    uuid           VARCHAR(255) primary key,
    `url`          VARCHAR(1000),
    `content-type` VARCHAR(255),
    `timestamp`    BIGINT
);

create table PUBLIC.`blob`
(
    uuid           VARCHAR(255) primary key,
    `data`         BINARY LARGE OBJECT,
    `content-type` VARCHAR(255)
);

create table PUBLIC.TEXT
(
    UUID           CHARACTER VARYING(255) not null primary key,
    DATA           CHARACTER LARGE OBJECT,
    `CONTENT-TYPE` CHARACTER VARYING(255)
);

create table PUBLIC.`code`
(
    type        VARCHAR(255) primary key,
    `code`      CHARACTER LARGE OBJECT,
    `timestamp` BIGINT
);

create table PUBLIC.`auth-token`
(
    kind    VARCHAR(255) primary key,
    `token` VARCHAR(255)
);

create table PUBLIC.`subscription`
(
    uuid        VARCHAR(255) primary key,
    `name`      VARCHAR(255),
    `topic`     VARCHAR(255),
    `callback`  VARCHAR(1000),
    `secret`    VARCHAR(255),
    `timestamp` BIGINT
);

create table PUBLIC.`history`
(
    uuid    VARCHAR(255) primary key,
    `items` CHARACTER LARGE OBJECT
);

create table PUBLIC.`sample`
(
    uuid   VARCHAR(255) primary key,
    `data` CHARACTER LARGE OBJECT
);

create table PUBLIC.`word-filter`
(
    id      VARCHAR(255) primary key,
    `words` CHARACTER LARGE OBJECT
);

create table PUBLIC.`log`
(
    kind        VARCHAR(255) primary key,
    `top-entry` BIGINT,
    `is-open`   BOOLEAN
);

create table PUBLIC.`log-entry`
(
    uuid        VARCHAR(255) primary key,
    `number`    BIGINT,
    `level`     VARCHAR(255),
    `source`    VARCHAR(255),
    `timestamp` BIGINT
);

create table PUBLIC.`log-message`
(
    uuid      VARCHAR(255) primary key,
    `message` CHARACTER LARGE OBJECT
);
